import socks
import socket

host = "change6.owlproxy.com"
port = 7778
username = "ZI5g9vM7cN90_custom_zone_FR_st__city_sid_92515164_time_5"
password = "5138017"

try:
    s = socks.socksocket()
    s.set_proxy(socks.SOCKS5, host, port, True, username, password)
    s.settimeout(5.0)
    print("Connecting to Telegram via proxy...")
    s.connect(("149.154.167.50", 443))
    print("Success! Proxy works.")
    s.close()
except Exception as e:
    print(f"Error: {e}")
