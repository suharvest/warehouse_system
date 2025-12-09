import requests
import json
import sys

BASE_URL = "http://localhost:2124/api"

def test_search():
    print(f"Testing search API at {BASE_URL}...")
    
    # 1. List all
    print("\n1. Listing all materials...")
    try:
        resp = requests.get(f"{BASE_URL}/materials/list", params={"page": 1, "page_size": 10})
        data = resp.json()
        print(f"Total: {data.get('total')}")
        if data.get('total') > 0:
            print("✅ List all success")
        else:
            print("⚠️ No materials found (maybe empty DB?)")
    except Exception as e:
        print(f"❌ Failed: {e}")
        return

    # 2. Filter by Status (normal)
    print("\n2. Filtering by status='normal'...")
    resp = requests.get(f"{BASE_URL}/materials/list", params={"status": "normal"})
    data = resp.json()
    items = data.get('items', [])
    print(f"Found: {len(items)} items")
    all_normal = all(item['status'] == 'normal' for item in items)
    if all_normal and len(items) > 0:
        print("✅ Status filter success")
    elif len(items) == 0:
        print("⚠️ No normal items found to verify")
    else:
        print("❌ Status filter failed (found non-normal items)")

    # 3. Filter by Name (xiaozhi)
    print("\n3. Filtering by name='xiaozhi'...")
    resp = requests.get(f"{BASE_URL}/materials/list", params={"name": "xiaozhi"})
    data = resp.json()
    items = data.get('items', [])
    print(f"Found: {len(items)} items")
    if len(items) > 0:
        print("✅ Name filter success")
    else:
        print("⚠️ No 'xiaozhi' items found")

if __name__ == "__main__":
    test_search()
