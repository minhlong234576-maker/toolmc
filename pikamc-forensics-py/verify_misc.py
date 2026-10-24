import sys; sys.path.insert(0, ".")
# 1) cache key with dict param ordering
d1 = {"file": "a", "limit": 1}
d2 = {"limit": 1, "file": "a"}
print("cache key equal for same params diff order:", f"{d1}" == f"{d2}")

# 2) CORS invalid combo
import app.main as m
for mw in m.app.user_middleware:
    print("middleware:", mw)
