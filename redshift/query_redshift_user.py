"""
Pull from Redshift and save a CSV.

  1. Fill in YOUR_USERNAME and YOUR_PASSWORD below.
  2. Run: python query_redshift.py
  3. A CSV lands in the same folder as this script.
"""

import os
import redshift_connector
import pandas as pd

# ============================================================
# FILL THESE IN
# ============================================================
USERNAME = "ro_pmcginn"
PASSWORD = "Mcginn&RO0515!"
# ============================================================

HOST = "hhs-dw-redshift-cluster-1.cync2jeee9c6.us-east-1.redshift.amazonaws.com"
DATABASE = "dwredshift1"
PORT = 5439
OUT_DIR = os.path.dirname(os.path.abspath(__file__))

# the query, change this part (or paste any of the .sql files)
QUERY = """
    SELECT table_name
    FROM information_schema.tables
    WHERE table_schema = 'aspa_analytics'
    ORDER BY table_name;    
    """

conn = redshift_connector.connect(
    host=HOST, database=DATABASE, user=USERNAME, password=PASSWORD, port=PORT,
)
cur = conn.cursor()
cur.execute(QUERY)
df = cur.fetch_dataframe()
conn.close()

print(df)
out = os.path.join(OUT_DIR, "tables.csv")
df.to_csv(out, index=False)
print("saved", out)
