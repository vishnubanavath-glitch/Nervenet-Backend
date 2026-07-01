import sys
from pathlib import Path

# Add mcp_server folder to python path
mcp_path = Path(__file__).parent / "mcp_server"
sys.path.append(str(mcp_path))

try:
    from server import (
        discover_schema,
        query,
        aggregate,
        search,
        statistics,
        sample_rows,
        create,
        update,
        delete,
        reload_cache,
        prepare_chart_data
    )
    print("Successfully imported tools from server.py")
except ImportError as e:
    print(f"Error importing tools: {e}")
    sys.exit(1)

def run_tests():
    print("\n=== STARTING DATABASE QUERY ENGINE VERIFICATION SUITE ===")

    print("\n--- Test 1: Discover Schema ---")
    schema = discover_schema()
    print("Schema output:", schema)
    assert "dataset_name" in schema, "dataset_name missing"
    assert "column_names" in schema, "column_names missing"
    assert "uidNo" in schema["column_names"], "uidNo should be in schema headers"
    print("Test 1 Passed.")

    print("\n--- Test 2: Sample Rows ---")
    samples = sample_rows(limit=3)
    print("Sample rows count:", samples.get("sample_size"))
    print("First sample record keys:", list(samples.get("records")[0].keys()) if samples.get("records") else "None")
    assert samples.get("sample_size") <= 3, "Sample limit exceeded"
    print("Test 2 Passed.")

    print("\n--- Test 3: High-Level Statistics ---")
    stats = statistics()
    print("Dataset stats:", stats)
    assert "total_rows" in stats, "total_rows missing"
    assert "duplicate_rows" in stats, "duplicate_rows missing"
    print("Test 3 Passed.")

    print("\n--- Test 4: Free-Text Search ---")
    # Search for something typical, e.g. "RASULGARH"
    search_res = search(query_text="RASULGARH", limit=3)
    print(f"Search found {search_res.get('count')} records.")
    for r in search_res.get("records", []):
        print(f"Matched UID: {r.get('uidNo')} subdivision: {r.get('subDiv')}")
    assert search_res.get("count") > 0, "Should find records for RASULGARH"
    print("Test 4 Passed.")

    print("\n--- Test 5: DB Engine Query (Filters, Projections, Sorting, Pagination) ---")
    # Query using new structured filter with operators
    q_res = query(filters=[{"column": "subDiv", "operator": "=", "value": "RASULGARH"}], columns=["uidNo", "mobileNo", "subDiv"], limit=5)
    print("Query results count:", q_res.get("count"))
    assert q_res.get("count") > 0, "Should find records for RASULGARH"
    if q_res.get("records"):
        sample_rec = q_res.get("records")[0]
        print("Projected record structure:", sample_rec)
        assert len(sample_rec) == 3, "Projection columns count mismatch"
        assert set(sample_rec.keys()) == {"uidNo", "mobileNo", "subDiv"}, "Incorrect projected fields"

    # Query using greater-than operator
    q_gt = query(filters=[{"column": "step_count", "operator": ">", "value": 125}], columns=["uidNo", "step_count"], limit=5)
    print("Query with step_count > 125 results count:", q_gt.get("count"))
    for r in q_gt.get("records", []):
        print(f"UID: {r.get('uidNo')} Step Count: {r.get('step_count')}")
        assert float(r.get("step_count")) > 125, "Greater than filter evaluation failed"
    print("Test 5 Passed.")

    print("\n--- Test 6: DB Engine Aggregation ---")
    # Aggregate counts of normal readings in RASULGARH subdivision
    count_agg = aggregate(operation="count", column="uidNo", filters=[{"column": "subDiv", "operator": "=", "value": "RASULGARH"}])
    print("Aggregation Count result:", count_agg)
    assert count_agg.get("operation") == "count"
    
    # Aggregate max step_count
    max_step = aggregate(operation="maximum", column="step_count")
    print("Aggregation Max step count result:", max_step)
    assert max_step.get("operation") == "maximum"
    print("Test 6 Passed.")

    print("\n--- Test 7: Create Record (with validations) ---")
    test_uid = "9999999"
    new_record = {
        "uidNo": test_uid,
        "mobileNo": "1234567890",
        "step_count": 120,
        "orientation": "Landscape",
        "readingStatus": "Normal",
        "subDiv": "TestDivision",
        "KWH.analysisRemark": "Verification Test Run"
    }

    # Pre-clean
    delete(test_uid)

    # Insert record
    create_res = create(new_record)
    print("Create record result:", create_res)
    assert create_res.get("status") == "Success", "Failed inserting record"

    # Read and verify projection matches
    check_q = query(filters={"uidNo": test_uid}, columns=["uidNo", "mobileNo", "step_count"])
    print("Fetched created record:", check_q)
    assert check_q.get("count") == 1, "Record not found"
    created_rec = check_q.get("records")[0]
    assert created_rec.get("uidNo") == test_uid, "UID mismatch"
    assert created_rec.get("mobileNo") == "1234567890", "Phone mismatch"
    print("Test 7 Passed.")

    print("\n--- Test 8: Update Record (with validations) ---")
    updates = {
        "mobileNo": "9876543210",
        "step_count": 555
    }
    update_res = update(uid=test_uid, updates=updates)
    print("Update result:", update_res)
    assert update_res.get("status") == "Success", "Update failed"

    # Verify updates
    check_q = query(filters={"uidNo": test_uid}, columns=["uidNo", "mobileNo", "step_count"])
    updated_rec = check_q.get("records")[0]
    assert updated_rec.get("mobileNo") == "9876543210", "Phone update failed"
    assert float(updated_rec.get("step_count")) == 555.0, "Step count update failed"
    print("Test 8 Passed.")

    print("\n--- Test 9: Delete Record & Reload Cache ---")
    delete_res = delete(test_uid)
    print("Delete result:", delete_res)
    assert delete_res.get("status") == "Success"

    # Verify record is gone
    check_q = query(filters={"uidNo": test_uid})
    assert check_q.get("count") == 0, "Record was not deleted"

    reload_res = reload_cache()
    print("Reload cache result:", reload_res)
    assert reload_res.get("status") == "Success"
    print("Test 9 Passed.")

    print("\n--- Test 10: Prepare Chart Data ---")
    # Prepare average step count by subdivision
    chart_res = prepare_chart_data(
        chart_type="bar",
        title="Average Step Count by Subdivision",
        x_axis="subDiv",
        y_axis="step_count",
        aggregation="average",
        limit=5
    )
    print("Prepare Chart Data result:", chart_res)
    assert chart_res.get("type") == "bar"
    assert chart_res.get("x_axis") == "subDiv"
    assert chart_res.get("y_axis") == "step_count"
    assert isinstance(chart_res.get("data"), list)
    assert len(chart_res.get("data")) <= 5
    print("Test 10 Passed.")

    print("\n=== ALL 10 DATABASE QUERY ENGINE TESTS PASSED SUCCESSFULLY! ===")

if __name__ == "__main__":
    run_tests()
