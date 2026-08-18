"""Run fanout_repro.preql against real rows and count them.

    <pytrilogy>/.venv/Scripts/python.exe fanout_repro.py

Prints the generated SQL, then the row count. 5 is correct, 11 is the bug.
Three of the five trees share species 'Quercus', so each of those fans out x3.
"""

from pathlib import Path

from trilogy import Dialects, Environment

MODEL = Path(__file__).parent / "fanout_repro.preql"

env = Environment(working_path=MODEL.parent)
ex = Dialects.DUCK_DB.default_executor(environment=env)
ex.execute_raw_sql(
    """
CREATE TABLE a_tree_info AS SELECT * FROM (VALUES
    ('a1','A','Quercus',10.0),
    ('a2','A','Quercus',12.0),
    ('a3','A','Tilia',8.0)
) t(tree_id, city, species, dbh);
CREATE TABLE b_tree_info AS SELECT * FROM (VALUES
    ('b1','B','Quercus',20.0),
    ('b2','B','Acer',5.0)
) t(tree_id, city, species, dbh);
"""
)

sql = ex.generate_sql(MODEL.read_text(encoding="utf-8"))[-1]
print(sql)
rows = ex.execute_raw_sql(sql).fetchall()
print(f"\n=== {len(rows)} rows returned; 5 is correct ===")
for row in sorted(rows):
    print("   ", row)
assert len(rows) == 5, f"FAN-OUT: {len(rows)} rows, expected 5"
