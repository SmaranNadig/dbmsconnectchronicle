"""Source adapters: read a release's files as published and emit normalized
frames that the loader copies into staging tables.

Every adapter is driven by a schema mapping (config/schema_mappings/*.yaml),
which is also registered in the cc.schema_mapping table, so the mapping from
source fields to canonical fields is data, not hidden code.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

import pandas as pd
import yaml

from backend.config import RAW_DIR, SCHEMA_MAPPING_DIR
from backend.etl.sources import SourceRef, find_source, open_source

CHUNK_ROWS = 500_000


class ValidationError(Exception):
    """Source data violates the adapter's expectations; nothing is loaded."""


@dataclass
class NeuronFile:
    ref: SourceRef
    id_column: str
    annotations: dict[str, str]          # source column -> canonical annotation key
    defines_neurons: bool
    missing_columns: list[str] = field(default_factory=list)
    unknown_neuron_policy: str = "error"     # 'error' | 'skip' (rows for ids absent from the neuron file)


@dataclass
class ConnectionFile:
    ref: SourceRef
    pre_column: str
    post_column: str
    weight_column: str
    duplicate_pairs: str                 # 'error' | 'sum'


def _read_header(ref: SourceRef) -> list[str]:
    with open_source(ref) as fh:
        sep = "\t" if ref.display.endswith(".tsv") else ","
        return list(pd.read_csv(fh, nrows=0, sep=sep).columns)


def _chunks(ref: SourceRef, usecols: list[str]) -> Iterator[pd.DataFrame]:
    """Yield string-typed chunks with a 1-based `_line` (header is line 1)."""
    line = 2
    with open_source(ref) as fh:
        for chunk in pd.read_csv(fh, usecols=usecols, dtype=str, keep_default_na=False,
                                 chunksize=CHUNK_ROWS):
            chunk["_line"] = range(line, line + len(chunk))
            line += len(chunk)
            yield chunk


class DatasetAdapter:
    """Base adapter. Subclasses may override the hooks for source quirks."""

    code: str = ""

    def __init__(self, release: str, release_dir: Path | None = None):
        self.release = release
        self.release_dir = release_dir or RAW_DIR / self.code / release
        with open(SCHEMA_MAPPING_DIR / f"{self.code}.yaml", encoding="utf-8") as fh:
            self.mapping = yaml.safe_load(fh)
        self.id_re = re.compile(self.mapping.get("id_pattern", r".+"))
        self._neuron_files: list[NeuronFile] | None = None
        self._connection_file: ConnectionFile | None = None

    # ------------------------------------------------------------ inspect
    def inspect_schema(self) -> dict:
        """Resolve mapped files and report their actual columns."""
        if not self.release_dir.is_dir():
            raise ValidationError(f"release directory not found: {self.release_dir}")
        report: dict = {"release_dir": str(self.release_dir), "files": []}
        neuron_files: list[NeuronFile] = []
        for spec in self.mapping["neuron_files"]:
            ref = find_source(self.release_dir, spec["file"])
            if ref is None:
                if spec.get("optional"):
                    report["files"].append({"file": spec["file"], "status": "absent (optional)"})
                    continue
                raise ValidationError(f"required file {spec['file']} not found in {self.release_dir}")
            cols = _read_header(ref)
            if spec["id_column"] not in cols:
                raise ValidationError(f"{ref.display}: id column {spec['id_column']!r} missing; has {cols}")
            present = {c: k for c, k in spec.get("annotations", {}).items() if c in cols}
            missing = [c for c in spec.get("annotations", {}) if c not in cols]
            neuron_files.append(NeuronFile(ref, spec["id_column"], present,
                                           bool(spec.get("defines_neurons")), missing,
                                           spec.get("unknown_neuron_policy", "error")))
            report["files"].append({"file": ref.display, "columns": cols, "mapped": present,
                                    "unmapped_missing": missing})
        if sum(f.defines_neurons for f in neuron_files) != 1:
            raise ValidationError("exactly one neuron file must define the neuron set")
        c = self.mapping["connections"]
        cref = find_source(self.release_dir, c["file"])
        if cref is None:
            raise ValidationError(f"connection file {c['file']} not found in {self.release_dir}")
        ccols = _read_header(cref)
        for col in (c["pre_column"], c["post_column"], c["weight_column"]):
            if col not in ccols:
                raise ValidationError(f"{cref.display}: column {col!r} missing; has {ccols}")
        self._neuron_files = neuron_files
        self._connection_file = ConnectionFile(cref, c["pre_column"], c["post_column"],
                                               c["weight_column"], c.get("duplicate_pairs", "error"))
        report["files"].append({"file": cref.display, "columns": ccols})
        return report

    def validate(self) -> dict:
        """Structural validation (files and columns). Row-level checks run in SQL."""
        return self.inspect_schema()

    @property
    def neuron_files(self) -> list[NeuronFile]:
        if self._neuron_files is None:
            self.inspect_schema()
        return self._neuron_files  # type: ignore[return-value]

    @property
    def connection_file(self) -> ConnectionFile:
        if self._connection_file is None:
            self.inspect_schema()
        return self._connection_file  # type: ignore[return-value]

    # ------------------------------------------------------------ extract
    def normalize_id(self, s: pd.Series) -> pd.Series:
        return s.str.strip()

    def normalize_value(self, key: str, s: pd.Series) -> pd.Series:
        return s.str.strip()

    def count_rows(self, ref: SourceRef) -> int:
        with open_source(ref) as fh:
            return sum(1 for _ in fh) - 1   # minus header

    def extract_neurons(self) -> Iterator[pd.DataFrame]:
        """Frames of (source_id, line) from the neuron-defining file."""
        nf = next(f for f in self.neuron_files if f.defines_neurons)
        for ch in _chunks(nf.ref, [nf.id_column]):
            yield pd.DataFrame({"source_id": self.normalize_id(ch[nf.id_column]), "line": ch["_line"]})

    def extract_annotations(self, nf: NeuronFile) -> Iterator[pd.DataFrame]:
        """Long frames of (source_id, key, value, line); empty values are dropped."""
        if not nf.annotations:
            return
        for ch in _chunks(nf.ref, [nf.id_column, *nf.annotations]):
            ids = self.normalize_id(ch[nf.id_column])
            parts = []
            for col, key in nf.annotations.items():
                v = self.normalize_value(key, ch[col])
                keep = v != ""
                parts.append(pd.DataFrame({"source_id": ids[keep], "key": key,
                                           "value": v[keep], "line": ch["_line"][keep]}))
            yield pd.concat(parts, ignore_index=True)

    def extract_connections(self) -> Iterator[pd.DataFrame]:
        """Frames of (pre, post, weight, line), one row per source row."""
        cf = self.connection_file
        for ch in _chunks(cf.ref, [cf.pre_column, cf.post_column, cf.weight_column]):
            yield pd.DataFrame({"pre": self.normalize_id(ch[cf.pre_column]),
                                "post": self.normalize_id(ch[cf.post_column]),
                                "weight": ch[cf.weight_column].str.strip(),
                                "line": ch["_line"]})


class HemibrainAdapter(DatasetAdapter):
    code = "hemibrain"


class FlyWireAdapter(DatasetAdapter):
    code = "flywire"


class MaleCNSAdapter(DatasetAdapter):
    """Template. MaleCNS (neuPrint dataset 'male-cns') needs a neuPrint token;
    export neurons (bodyId,type,instance) and pair weights to CSV, add
    config/schema_mappings/malecns.yaml and a catalog entry, then ingest."""
    code = "malecns"


class MANCAdapter(DatasetAdapter):
    """Template. MANC (neuPrint dataset 'manc') - same procedure as MaleCNS."""
    code = "manc"


ADAPTERS: dict[str, type[DatasetAdapter]] = {
    a.code: a for a in (HemibrainAdapter, FlyWireAdapter, MaleCNSAdapter, MANCAdapter)
}


def get_adapter(dataset: str, release: str, release_dir: Path | None = None) -> DatasetAdapter:
    mapping_file = SCHEMA_MAPPING_DIR / f"{dataset}.yaml"
    if not mapping_file.exists():
        raise ValidationError(f"{dataset}: no schema mapping {mapping_file}")
    # a dataset with a mapping but no dedicated class gets the generic,
    # mapping-driven adapter: new sources need configuration, not new code
    cls = ADAPTERS.get(dataset) or type(f"{dataset.title()}Adapter", (DatasetAdapter,), {"code": dataset})
    return cls(release, release_dir)
