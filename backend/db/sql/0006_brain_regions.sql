-- Brain regions: a shared region vocabulary and, per release, how many
-- synapses each neuron has in each region.
--
-- Sources: hemibrain `traced-roi-connections.csv` (roi codes like 'SLP(R)')
-- and FlyWire `connections.csv` (neuropil codes like 'SLP_R'). Both are
-- mapped through brain_region_alias to one canonical code + hemisphere.
-- hemibrain splits the mushroom-body lobes more finely (aL, a'L -> MB_VL;
-- bL, b'L, gL -> MB_ML) than FlyWire; the alias keeps the original code.
--
-- neuron_region_synapses is a derived per-release aggregate (not
-- bitemporal): it is rebuilt from the checksummed source file of that release
-- and points at its provenance record.
SET search_path = cc, public;

ALTER TABLE brain_region ADD COLUMN name text;

-- canonical vocabulary (FlyWire neuropil naming)
INSERT INTO brain_region (canonical_code, hemisphere, super_region, name)
SELECT code, side, CASE WHEN code IN ('ME', 'LO', 'LOP', 'LA', 'AME') THEN 'optic'
                        WHEN code IN ('UNASSIGNED', 'OCG') THEN 'other' ELSE 'central' END, name
FROM (VALUES
    ('AB', 'asymmetrical body'), ('AL', 'antennal lobe'), ('AME', 'accessory medulla'),
    ('AMMC', 'antennal mechanosensory and motor center'), ('AOTU', 'anterior optic tubercle'),
    ('ATL', 'antler'), ('AVLP', 'anterior ventrolateral protocerebrum'), ('BU', 'bulb'),
    ('CAN', 'cantle'), ('CRE', 'crepine'), ('EPA', 'epaulette'), ('FLA', 'flange'), ('GA', 'gall'),
    ('GOR', 'gorget'), ('IB', 'inferior bridge'), ('ICL', 'inferior clamp'), ('IPS', 'inferior posterior slope'),
    ('LA', 'lamina'), ('LAL', 'lateral accessory lobe'), ('LH', 'lateral horn'), ('LO', 'lobula'),
    ('LOP', 'lobula plate'), ('MB_CA', 'mushroom body calyx'), ('MB_ML', 'mushroom body medial lobe'),
    ('MB_PED', 'mushroom body pedunculus'), ('MB_VL', 'mushroom body vertical lobe'), ('ME', 'medulla'),
    ('PLP', 'posterior lateral protocerebrum'), ('PVLP', 'posterior ventrolateral protocerebrum'),
    ('SCL', 'superior clamp'), ('SIP', 'superior intermediate protocerebrum'),
    ('SLP', 'superior lateral protocerebrum'), ('SMP', 'superior medial protocerebrum'),
    ('SPS', 'superior posterior slope'), ('VES', 'vest'), ('WED', 'wedge')
) paired(code, name)
CROSS JOIN (VALUES ('L'), ('R')) s(side)
UNION ALL
SELECT code, NULL, CASE WHEN code IN ('UNASSIGNED', 'OCG') THEN 'other' ELSE 'central' END, name
FROM (VALUES
    ('EB', 'ellipsoid body'), ('FB', 'fan-shaped body'), ('NO', 'noduli'), ('PB', 'protocerebral bridge'),
    ('GNG', 'gnathal ganglia'), ('SAD', 'saddle'), ('PRW', 'prow'), ('OCG', 'ocellar ganglion'),
    ('IB', 'inferior bridge (unpaired in hemibrain)'),
    ('UNASSIGNED', 'synapses outside any named primary region')
) unpaired(code, name);

CREATE TABLE neuron_region_synapses (
    release_id         integer NOT NULL,
    dataset_id         smallint NOT NULL,
    dataset_neuron_id  bigint NOT NULL,
    brain_region_id    integer NOT NULL REFERENCES brain_region,
    pre_synapses       integer NOT NULL CHECK (pre_synapses >= 0),    -- outgoing synapses in the region
    post_synapses      integer NOT NULL CHECK (post_synapses >= 0),   -- incoming synapses in the region
    provenance_id      bigint NOT NULL REFERENCES provenance,
    PRIMARY KEY (release_id, dataset_neuron_id, brain_region_id),
    CHECK (pre_synapses + post_synapses > 0),
    FOREIGN KEY (release_id, dataset_id) REFERENCES release (release_id, dataset_id),
    FOREIGN KEY (dataset_neuron_id, dataset_id) REFERENCES dataset_neuron (dataset_neuron_id, dataset_id)
);
CREATE INDEX neuron_region_synapses_region_idx ON neuron_region_synapses (brain_region_id, release_id);

-- The region where a neuron has most synapses in a release (ties: lowest id).
CREATE VIEW neuron_primary_region AS
SELECT DISTINCT ON (release_id, dataset_neuron_id)
       release_id, dataset_id, dataset_neuron_id, brain_region_id,
       pre_synapses + post_synapses AS synapses
FROM neuron_region_synapses
ORDER BY release_id, dataset_neuron_id, pre_synapses + post_synapses DESC, brain_region_id;

CREATE VIEW brain_region_label AS
SELECT brain_region_id, canonical_code || coalesce('_' || hemisphere, '') AS code, name, super_region
FROM brain_region;
