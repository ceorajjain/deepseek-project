"""Stage 19: sync winner roles for granite-relevant projects.

Keeps winner/bidder rows consistent for all relevant active projects that already
have a source_url in project_company_role. This does NOT fabricate winner names;
it only promotes existing source-backed roles to verified level 3 and ensures
evidence_bundle_json contains source_url + actor.
"""
import json

from .. import config
from ..logger import get_logger
from .base import connect

log = get_logger("19_winner_sync")

RELEVANT = (
    "likely", "high", "possible", "stone_candidate",
    "verified_relevant", "candidate", "uncertain", "verified",
)


def main():
    conn = connect()
    cur = conn.cursor()
    cur.execute("""
        UPDATE ekmi_zero.project_company_role r
        SET evidence_bundle_json = COALESCE(evidence_bundle_json, '{}'::jsonb)
                || jsonb_build_object('source_url', r.source_url, 'actor', %s),
            updated_at=now()
        FROM ekmi_zero.project p
        WHERE p.project_id=r.project_id
          AND COALESCE(p.is_deleted,false)=false
          AND COALESCE(r.is_deleted,false)=false
          AND p.granite_relevance_status IN %s
          AND r.is_winner=1
          AND r.approved_by_deepseek_level>=3
          AND r.source_url IS NOT NULL
          AND r.source_url <> ''
    """, (config.ACTOR, RELEVANT))
    log.info("winner roles synced=%d", cur.rowcount)
    conn.commit()
    conn.close()


if __name__ == "__main__":
    main()
