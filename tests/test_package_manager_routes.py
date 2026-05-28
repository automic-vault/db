import unittest

from scripts.bootstrap.lib.managers import MANAGER_DEFINITIONS, manager_matcher, package_manager_routes


def route_map(name):
    index = {
        "managers": {
            "winget": {
                "display_name": "winget",
                "platform": "windows",
                "command_template": "winget install --id {id} -e",
                "source_label": "test",
                "packages": {
                    "Python.Python.3.14": {
                        "id": "Python.Python.3.14",
                        "match_names": ["python-3-14"],
                    }
                },
            },
            "macports": {
                "display_name": "MacPorts",
                "platform": "macos",
                "command_template": "sudo port install {id}",
                "source_label": "test",
                "packages": {
                    "python314": {"id": "python314", "match_names": ["python314"]},
                    "openssl3": {"id": "openssl3", "match_names": ["openssl-3"]},
                },
            },
            "debian": {
                "display_name": "Debian apt",
                "platform": "linux",
                "command_template": "sudo apt install {id}",
                "source_label": "test",
                "packages": {
                    "python3": {"id": "python3", "match_names": ["python"]},
                    "openssl": {"id": "openssl", "match_names": ["openssl"]},
                },
            },
        }
    }
    return {
        route["manager_key"]: (route["package_id"], route["match_tier"])
        for route in package_manager_routes(name, [], manager_matcher(index))
    }


class PackageManagerRouteTests(unittest.TestCase):
    def test_python_versioned_routes_prefer_exact_then_mark_fallbacks(self):
        routes = route_map("python@3.14")
        self.assertEqual(routes["winget"], ("Python.Python.3.14", "exact"))
        self.assertEqual(routes["macports"], ("python314", "exact"))
        self.assertEqual(routes["debian"], ("python3", "fallback"))

    def test_openssl_versioned_routes_use_ecosystem_aliases(self):
        routes = route_map("openssl@3")
        self.assertEqual(routes["macports"], ("openssl3", "exact"))
        self.assertEqual(routes["debian"], ("openssl", "fallback"))

    def test_install_lines_are_derivable_from_package_manager_values(self):
        package_manager = {
            "brew": "awscli",
            "debian": "awscli",
            "nix": "awscli",
        }
        commands = {
            "av": f"sudo av install brew:{package_manager['brew']}",
            "brew": f"brew install {package_manager['brew']}",
        }
        for manager, package_id in package_manager.items():
            if manager == "brew":
                continue
            commands[manager] = MANAGER_DEFINITIONS[manager]["command_template"].format(id=package_id)
        self.assertEqual(commands["av"], "sudo av install brew:awscli")
        self.assertEqual(commands["brew"], "brew install awscli")
        self.assertEqual(commands["debian"], "sudo apt install awscli")
        self.assertEqual(commands["nix"], "nix profile install nixpkgs#awscli")


if __name__ == "__main__":
    unittest.main()
