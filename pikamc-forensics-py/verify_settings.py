import sys
sys.path.insert(0, ".")
from app.database import SessionLocal, init_db
init_db()
from app.services.settings_service import set_setting, get_setting

db = SessionLocal()
cases = ["hello", "123", "true", "null", '{"a":1}', "[]", 42, True, 3.14, None, {"k":"v"}, ["x"]]
for v in cases:
    set_setting(db, "probe", v)
    got = get_setting(db, "probe")
    same = type(got) == type(v) and got == v
    print(f"set={v!r:20} ({type(v).__name__:5}) -> get={got!r:20} ({type(got).__name__:5}) roundtrip={same}")
    if not same:
        print(f"   !!! BUG: {v!r} -> {got!r}")

# non-serializable
try:
    print("nonserializable ->", set_setting(db, "probe", object()))
except Exception as e:
    print("nonserializable RAISED:", type(e).__name__, e)
db.close()
