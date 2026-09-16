import psycopg2

from . import config


def connect(readonly=False):
    conn = psycopg2.connect(**config.DB)
    if readonly:
        conn.set_session(readonly=True, autocommit=True)
    return conn


def count_approved_items():
    conn = connect(readonly=True)
    cur = conn.cursor()
    cur.execute("""
        SELECT approved_by_deepseek_level, count(*)
        FROM ekmi_zero.project_boq_item
        WHERE verified_by_raj='FRESH'
        GROUP BY 1 ORDER BY 1
    """)
    rows = cur.fetchall()
    conn.close()
    return rows
