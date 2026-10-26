from app import db


def main() -> None:
    db.init_db()
    with db.db_connection() as connection:
        overall = connection.execute(
            """SELECT e.arm, 'overall' AS segment, COUNT(*) AS exposures,
                      SUM(CASE WHEN s.liked=1 THEN 1 ELSE 0 END) AS keeps
               FROM deck_events e LEFT JOIN swipes s
                 ON s.profile_id=e.profile_id AND s.item_id=e.item_id
               GROUP BY e.arm ORDER BY e.arm"""
        ).fetchall()
        first_twenty = connection.execute(
            """SELECT e.arm, 'cards_1_20' AS segment, COUNT(*) AS exposures,
                      SUM(CASE WHEN s.liked=1 THEN 1 ELSE 0 END) AS keeps
               FROM deck_events e LEFT JOIN swipes s
                 ON s.profile_id=e.profile_id AND s.item_id=e.item_id
               WHERE e.position BETWEEN 1 AND 20
               GROUP BY e.arm ORDER BY e.arm"""
        ).fetchall()
        rows = [*first_twenty, *overall]
    print("arm\tsegment\tkeeps\texposures\tkeep_rate")
    for row in rows:
        exposures = row["exposures"]
        keeps = row["keeps"] or 0
        rate = keeps / exposures if exposures else 0.0
        print(f"{row['arm']}\t{row['segment']}\t{keeps}\t{exposures}\t{rate:.4f}")


if __name__ == "__main__":
    main()
