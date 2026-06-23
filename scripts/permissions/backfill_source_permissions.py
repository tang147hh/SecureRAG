from ktem.permissions.backfill import backfill_source_permissions


if __name__ == "__main__":
    total = backfill_source_permissions()
    print(f"Backfilled permissions for {total} indexed sources.")
