from actions.attack_chain import extract_nmap_services, extract_technologies


def test_extract_nmap_services():
    raw = "80/tcp  open  http  Apache httpd 2.4.49"
    services = extract_nmap_services(raw)
    assert len(services) == 1
    assert services[0]["product"] == "Apache httpd"
    assert services[0]["version"] == "2.4.49"


def test_extract_technologies():
    techs = extract_technologies(["Server: Apache", "X-Powered-By: PHP/7.4.3"])
    assert len(techs) == 2
    assert techs[0]["product"] == "Apache"
    assert techs[1]["version"] == "7.4.3"