from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path


DEFAULT_DB = Path("ktem_app_data/user_data/sql.db")

SOURCE_ALIASES = {
    "long_policy_mixed.md": "星河智造科技有限公司综合运营制度手册_无问题版.md",
}


def source_tables(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "select name from sqlite_master where type='table' and name like 'index__%__source'"
    ).fetchall()
    return [row[0] for row in rows]


def source_id_by_name(conn: sqlite3.Connection) -> dict[str, str]:
    output: dict[str, str] = {}
    for table in source_tables(conn):
        rows = conn.execute(f'select id, name from "{table}"').fetchall()
        for source_id, name in rows:
            if name and name not in output:
                output[str(name)] = str(source_id)
    return output


def remap_source_ids(values: list[str], mapping: dict[str, str]) -> tuple[list[str], bool]:
    changed = False
    output: list[str] = []
    for value in values:
        target_name = SOURCE_ALIASES.get(value, value)
        next_value = mapping.get(target_name, value)
        changed = changed or next_value != value
        if next_value not in output:
            output.append(next_value)
    return output, changed


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Map eval expected_source_ids from file names to current source ids."
    )
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--apply", action="store_true", help="Write changes to DB.")
    args = parser.parse_args()

    conn = sqlite3.connect(args.db)
    mapping = source_id_by_name(conn)
    rows = conn.execute(
        "select id, question, expected_source_ids from ktem__rag_eval_example"
    ).fetchall()

    updates: list[tuple[str, str, list[str], list[str]]] = []
    for example_id, question, raw_expected in rows:
        expected = json.loads(raw_expected or "[]")
        remapped, changed = remap_source_ids(expected, mapping)
        if changed:
            updates.append((str(example_id), str(question), expected, remapped))

    for _, question, before, after in updates:
        print(f"{question}: {before} -> {after}")

    if args.apply:
        for example_id, _, _, after in updates:
            conn.execute(
                "update ktem__rag_eval_example set expected_source_ids=? where id=?",
                (json.dumps(after, ensure_ascii=False), example_id),
            )
        conn.commit()
        print(f"Updated {len(updates)} examples.")
    else:
        print(f"Dry run: {len(updates)} examples would be updated. Use --apply to write.")


if __name__ == "__main__":
    main()
