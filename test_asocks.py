import socks
import socket

host = "45.14.135.182"
port = 443
username = "u3wmvsku7a-corp.mobile.res-country-FR-hold-session-session-6a55988538906"
password = "JTGyaAiJ7gEJZfq6"

def test_proxy(ptype, name):
    try:
        s = socks.socksocket()
        s.set_proxy(ptype, host, port, True, username, password)
        s.settimeout(10.0)
        s.connect(("149.154.167.50", 443))
        print(f"Success with {name}!")
        s.close()
    except Exception as e:
        print(f"Failed with {name}: {e}")

test_proxy(socks.SOCKS5, "SOCKS5")
test_proxy(socks.HTTP, "HTTP")
