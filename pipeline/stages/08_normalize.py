"""Stage 8: normalize SQM -> SQFT for demand aggregation (quantity_sqft field)."""
from ..logger import get_logger
from .base import connect

log = get_logger("08_normalize")
SQM_TO_SQFT = 10.7639104


def main():
    conn = connect()
    cur = conn.cursor()
    # SQM -> SQFT
    cur.execute("""
        UPDATE ekmi_zero.project_boq_item
        SET quantity_sqft = ROUND((quantity * %s)::numeric, 2), normalized_quantity=quantity,
            normalized_unit='SQM', updated_at=now()
        WHERE verified_by_raj='FRESH' AND approved_by_deepseek_level=3
          AND upper(coalesce(unit,''))='SQM' AND quantity IS NOT NULL
    """, (SQM_TO_SQFT,))
    sqm = cur.rowcount
    # SQFT stays as-is
    cur.execute("""
        UPDATE ekmi_zero.project_boq_item
        SET quantity_sqft=quantity, normalized_quantity=quantity,
            normalized_unit='SQFT', updated_at=now()
        WHERE verified_by_raj='FRESH' AND approved_by_deepseek_level=3
          AND upper(coalesce(unit,''))='SQFT' AND quantity IS NOT NULL
    """)
    sqft = cur.rowcount
    conn.commit()
    conn.close()
    log.info("normalize done: sqm=%d sqft=%d", sqm, sqft)


if __name__ == "__main__":
    main()
