# Data editor: implementation plan

## Requirement

The evaluator wants to work on the database directly from the website, not
only read it:

- run any SQL, including `INSERT`, `UPDATE`, `DELETE`, `CREATE TABLE`,
  transactions;
- see the tables and their rows;
- add, change and delete records;
- do the same on the NoSQL side (MongoDB: insert, update, delete documents,
  indexes, collections).

## Constraint

The real database holds 5 public releases (182,274 neurons, 9.0 M connection
rows, about 4 GB) that took about 30 minutes to load and are the evidence for
every result in the report. The history tables are append-only by design (a
trigger rejects `UPDATE` and `DELETE`). Giving a web console unrestricted
write access to them would let one mistaken statement destroy that evidence,
and most writes would be rejected by the history trigger anyway.

## Options considered

| Option | Verdict |
|---|---|
| A. Let the console write to the real tables | Rejected: one `DELETE` or `DROP` can destroy the loaded data; no way to undo; most writes are rejected by the append-only trigger, so the teacher cannot really "edit". |
| B. A full copy of the database for editing | Rejected: copying about 4 GB is slow, doubles disk use, and a reset would take minutes. |
| C. Rolled-back writes on the real tables only | Useful but not enough: shows constraints and triggers, but nothing the teacher adds stays. |
| **D. Two write targets: rolled-back trial on the real tables (C) + a persistent lab schema with a real-data subset** | **Chosen.** The teacher can change data permanently (in the lab), and can also see exactly how PostgreSQL treats a write on the real tables, without risk. |

## Design

### SQL: three targets in the query console

| Target | Runs as | What is allowed | Effect |
|---|---|---|---|
| Read (existing) | `cc_reader` (SELECT only) | one SELECT / WITH / EXPLAIN / SHOW | READ ONLY transaction, rolled back |
| Trial on the real tables | `cc_tryout` (SELECT, INSERT, UPDATE, DELETE on `cc`) | one statement, sent as a prepared statement so `COMMIT` cannot be smuggled in | runs with all real constraints and triggers, reports rows affected and `RETURNING` rows, then is always rolled back |
| Lab (saved) | `cc_lab`, a separate login role | any SQL script: DML, DDL in schema `lab`, `BEGIN` / `COMMIT` / `ROLLBACK`, several statements | committed and kept; optional "preview and roll back" |

`cc_lab` connects with its own login, so it cannot switch back to the
administrator role (unlike `SET ROLE`, which a script could undo with
`RESET ROLE`). It owns schema `lab`, can read `cc` and `er`, and has no write
privilege anywhere else, so even `DROP SCHEMA cc` or `DELETE FROM
cc.connection` is refused by PostgreSQL itself. Its password is derived from
the administrator password, so no extra setting is needed. Every statement
has a 10 s timeout, enforced on the server and by a watchdog that cancels the
query.

### The lab schema: a small, real, editable copy

Rebuilt from the real data by "Reset lab" (and on first use):

- `lab.dataset`, `lab.release`: every dataset and release.
- `lab.neuron`: for each dataset, the neurons in the strongest 150
  connections of the latest release, with cell type, side and primary brain
  region. IDs are the real `dataset_neuron_id`, so lab rows join to `cc`.
- `lab.connection`: the connections among those neurons in every release.
- `lab.change_log`: filled by an `AFTER INSERT OR UPDATE OR DELETE` trigger
  on the four tables, with old and new row as JSON, the user and the time.
- view `lab.neuron_degree`.

Same kinds of constraints as the main schema: primary keys, `UNIQUE`,
`CHECK` (positive synapse counts, side in L/R/center, code format), and
composite foreign keys so a connection cannot join neurons of two datasets.
Deleting a neuron cascades to its connections. The schema is small (a few
thousand rows), so a reset takes seconds.

### Table editor (no SQL needed)

A "Data editor" page lists the tables of `lab` (editable) and of `cc` / `er`
(browse only), shows columns, keys, row counts and paged, sortable rows.
For lab tables: "Add row" (a form built from the column list), "Edit" and
"Delete" per row. Each action shows the exact SQL that was executed, so the
form and the SQL console teach the same thing. PostgreSQL error messages
(constraint name, detail) are shown as they are.

### MongoDB

| Target | Allowed |
|---|---|
| Main (`connectome_meta`) | find, count, aggregate only (unchanged) |
| Lab (`connectome_meta_lab`) | find, count, aggregate, insertOne, insertMany, updateOne, updateMany, replaceOne, deleteOne, deleteMany, createIndex, dropIndex, listIndexes, createCollection (with optional `$jsonSchema` validator), dropCollection, listCollections |

The lab database is rebuilt by "Reset lab" with the source documents of the
same lab neurons, the same unique indexes as the main store, and a
`$jsonSchema` validator, so schema validation in a document database can be
shown. The API only performs writes on the lab database; operators that run
server-side code (`$where`, `$function`, `$accumulator`) and `$out` / `$merge`
stay refused everywhere. The Data editor shows the lab collections and
documents with Insert, Edit (JSON) and Delete buttons.

### Access

The site binds to localhost. If `CC_REVIEW_TOKEN` is set (the existing
reviewer token), every lab write and reset needs it, like other changing
actions.

## Work plan

1. Migration 0012: roles `cc_lab` (login, password derived) and `cc_tryout`;
   grants.
2. `backend/lab.py`: lab build/reset (SQL + Mongo), connection as `cc_lab`,
   script runner with per-statement results, table catalog, row CRUD.
3. Console: `target` for SQL (read / trial / lab) and MongoDB (main / lab),
   new MongoDB write operations.
4. API endpoints: `/lab/status`, `/lab/reset`, `/lab/tables`,
   `/lab/tables/{schema}/{table}/rows` (GET, POST, PATCH, DELETE),
   `/lab/changes`, `/lab/mongo/collections`.
5. Dashboard: target selectors in the query console; new Data editor page;
   write examples.
6. Tests: lab built from fixtures; CRUD and the SQL shown; constraint errors;
   change log; `cc_lab` cannot write `cc`, cannot become another role;
   trial writes leave no trace and the history trigger fires; MongoDB lab CRUD,
   validator, main store refuses writes; reset restores.
7. Docs: README, IMPLEMENTATION, TESTING.

## Acceptance

- The teacher can insert, update and delete rows and documents, create and
  drop tables, indexes and collections, and the changes stay after a page
  reload.
- The same statements against the real tables either run and are rolled back
  (with the effect shown) or are rejected by PostgreSQL, and the real row
  counts never change.
- Reset brings the lab back to the original subset.
- The full test suite passes.
