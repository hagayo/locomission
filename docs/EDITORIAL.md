# Human review of history missions

These are unapproved editorial drafts, not production historical facts.
The seeded locations may be approximate. They must be surveyed and checked
against public access and photography safety before any approval.

From api/ with DATABASE_URL configured, run:

    python -m locomission.editorial drafts

Only a real human reviewer may approve a mission after:
- Checking the original source against each historical claim
- Reviewing natural Hebrew and creative instructions
- Visiting or reliably verifying an accessible safe photo location
- Testing each visual criterion against normal camera photos

An example command structure (NOT executable until placeholders are replaced):

    python -m locomission.editorial approve jaffa-clock-tower --reviewer HUMAN_EDITOR --lat VERIFIED_LAT --lon VERIFIED_LON --radius-m VERIFIED_RADIUS --source-reviewed --hebrew-reviewed --safe-photo-site-reviewed --visual-criteria-reviewed

Approval requires the four check flags and verified coordinates. It records
the actual reviewer's name and UTC timestamp in PostgreSQL. Duplicate
approval requests are rejected. No AI or build pipeline may approve missions.

Do not treat supplied source URLs or approximate GPS points as already verified.


## Catalog workflow for the next pilot candidates

Five new location-based candidates exist at `content/tel-aviv-pilot-drafts.json`, with municipality source URLs and **approximate** GPS placeholders. Source pages were inspected as research inputs, but **no human editor has reviewed or approved these missions** and no onsite safety assessment occurred.

From repository root:

```sh
python -m pip install -e ./api
python -m locomission.catalog --file content/tel-aviv-pilot-drafts.json
python -m locomission.catalog --file content/tel-aviv-pilot-drafts.json --apply
```

The first command validates offline; the second requires `DATABASE_URL` and inserts only new **draft** rows. It never modifies any existing slug, including reviewed or approved missions. Missing/duplicated JSON fields, non-Hebrew copy, unsafe/incomplete criteria, invalid source URLs and coordinates are rejected before database writes. Check every fact and field condition manually before using the existing `approve` command. See `docs/PILOT-RELEASE.md`.
