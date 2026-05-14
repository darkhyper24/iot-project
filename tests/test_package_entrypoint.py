import importlib


def test_importing_package_entrypoint_has_no_side_effects():
    importlib.import_module("simulator.__main__")
