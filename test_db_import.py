import sys
try:
    import database
    print("SUCCESS")
    print(database.__file__)
except Exception as e:
    print("FAILED:", e)
