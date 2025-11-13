#!/usr/bin/env python3
"""
Basic test script for CRUD managers.
Tests config-only resources without requiring actual Kubernetes cluster.
"""
import sys
from pathlib import Path


def test_imports():
    """Test that all modules can be imported."""
    print("Testing imports...")
    
    try:
        from k8s_modules import resource_store
        print("  ✓ resource_store")
        
        from k8s_modules.base_resource import BaseResourceManager
        print("  ✓ BaseResourceManager")
        
        from k8s_modules.resources.config_only import ConfigOnlyResourceManager
        print("  ✓ ConfigOnlyResourceManager")
        
        from k8s_modules.resources.runtimes import RuntimesManager
        print("  ✓ RuntimesManager")
        
        from k8s_modules.resources.background import BackgroundManager
        print("  ✓ BackgroundManager")
        
        from k8s_modules.registry import (
            get_manager,
            ResourceRegistry,
            list_resource_groups
        )
        print("  ✓ registry")
        
        return True
    except ImportError as e:
        print(f"  ✗ Import error: {e}")
        return False


def test_resource_store_utilities():
    """Test resource_store utility functions."""
    print("\nTesting resource_store utilities...")
    
    from k8s_modules.resource_store import (
        sanitize_name,
        get_value_at_path,
        set_value_at_path,
        delete_value_at_path,
        is_secret_field_by_convention,
        extract_secret_fields,
        create_resource_labels
    )
    
    # Test sanitize_name
    assert sanitize_name("My_Runtime-ID") == "my-runtime-id"
    assert sanitize_name("UPPERCASE") == "uppercase"
    assert sanitize_name("test@email.com") == "test-email-com"
    print("  ✓ sanitize_name")
    
    # Test path operations
    data = {"a": {"b": {"c": "value"}}}
    assert get_value_at_path(data, "a.b.c") == "value"
    assert get_value_at_path(data, "a.b.d") is None
    print("  ✓ get_value_at_path")
    
    set_value_at_path(data, "a.b.d", "new")
    assert data["a"]["b"]["d"] == "new"
    print("  ✓ set_value_at_path")
    
    delete_value_at_path(data, "a.b.c")
    assert "c" not in data["a"]["b"]
    print("  ✓ delete_value_at_path")
    
    # Test secret detection
    assert is_secret_field_by_convention("api_key") == True
    assert is_secret_field_by_convention("password") == True
    assert is_secret_field_by_convention("token") == True
    assert is_secret_field_by_convention("normal_field") == False
    print("  ✓ is_secret_field_by_convention")
    
    # Test secret extraction
    spec = {
        "name": "test",
        "config": {
            "api_key": "secret123",
            "url": "https://api.com",
            "password": "pass456"
        }
    }
    public_spec, secret_data = extract_secret_fields(spec)
    assert "api_key" not in public_spec["config"]
    assert "password" not in public_spec["config"]
    assert "url" in public_spec["config"]
    assert "config.api_key" in secret_data
    assert "config.password" in secret_data
    print("  ✓ extract_secret_fields")
    
    # Test labels
    labels = create_resource_labels("runtimes", "my-runtime")
    assert labels["app.kubernetes.io/part-of"] == "agentbox"
    assert labels["agentbox.io/resource-group"] == "runtimes"
    assert labels["agentbox.io/resource-name"] == "my-runtime"
    print("  ✓ create_resource_labels")
    
    return True


def test_registry():
    """Test registry functions."""
    print("\nTesting registry...")
    
    from k8s_modules.registry import list_resource_groups
    
    groups = list_resource_groups()
    assert "runtimes" in groups
    assert "agents" in groups
    assert "background" in groups
    assert "channels" in groups
    assert "models" in groups
    print(f"  ✓ list_resource_groups ({len(groups)} groups)")
    
    # Print all available groups
    print(f"\n  Available resource groups:")
    for group in sorted(groups):
        print(f"    - {group}")
    
    return True


def test_schema_loading():
    """Test that schemas can be loaded."""
    print("\nTesting schema loading...")
    
    import json
    from pathlib import Path
    
    schema_dir = Path(__file__).parent / "schemas"
    
    if not schema_dir.exists():
        print("  ✗ schemas directory not found")
        return False
    
    schemas_found = 0
    for schema_file in schema_dir.glob("*-schema.json"):
        try:
            with open(schema_file, 'r') as f:
                schema = json.load(f)
                schemas_found += 1
        except Exception as e:
            print(f"  ✗ Error loading {schema_file.name}: {e}")
            return False
    
    print(f"  ✓ Successfully loaded {schemas_found} schemas")
    return True


def test_name_extraction():
    """Test name extraction from specs."""
    print("\nTesting name extraction...")
    
    from k8s_modules.resources.config_only import ConfigOnlyResourceManager
    
    # Create a mock manager (won't connect to k8s)
    class TestManager:
        def __init__(self):
            from k8s_modules.base_resource import BaseResourceManager
            self.resource_group = "test"
            self._extract_name = BaseResourceManager._extract_name.__get__(self, TestManager)
    
    mgr = TestManager()
    
    # Test various name fields
    spec1 = {"metadata": {"runtime_id": "rt-1"}}
    assert mgr._extract_name(spec1) == "rt-1"
    print("  ✓ Extract from metadata.runtime_id")
    
    spec2 = {"metadata": {"id": "id-1"}}
    assert mgr._extract_name(spec2) == "id-1"
    print("  ✓ Extract from metadata.id")
    
    spec3 = {"metadata": {"name": "name-1"}}
    assert mgr._extract_name(spec3) == "name-1"
    print("  ✓ Extract from metadata.name")
    
    spec4 = {"id": "id-2"}
    assert mgr._extract_name(spec4) == "id-2"
    print("  ✓ Extract from id")
    
    spec5 = {"name": "name-2"}
    assert mgr._extract_name(spec5) == "name-2"
    print("  ✓ Extract from name")
    
    try:
        mgr._extract_name({})
        print("  ✗ Should have raised error for missing name")
        return False
    except ValueError:
        print("  ✓ Raises error for missing name")
    
    return True


def test_validation():
    """Test spec validation."""
    print("\nTesting validation...")
    
    try:
        import jsonschema
        print("  ✓ jsonschema available")
    except ImportError:
        print("  ✗ jsonschema not installed")
        return False
    
    # Test validation with a simple schema
    import json
    schema = {
        "type": "object",
        "required": ["name"],
        "properties": {
            "name": {"type": "string"}
        }
    }
    
    valid_spec = {"name": "test"}
    invalid_spec = {"name": 123}
    
    try:
        jsonschema.validate(valid_spec, schema)
        print("  ✓ Valid spec passes")
    except jsonschema.ValidationError:
        print("  ✗ Valid spec rejected")
        return False
    
    try:
        jsonschema.validate(invalid_spec, schema)
        print("  ✗ Invalid spec accepted")
        return False
    except jsonschema.ValidationError:
        print("  ✓ Invalid spec rejected")
    
    return True


def main():
    """Run all tests."""
    print("=" * 60)
    print("AgentBox CRUD Managers - Basic Tests")
    print("=" * 60)
    
    tests = [
        ("Imports", test_imports),
        ("Resource Store Utilities", test_resource_store_utilities),
        ("Registry", test_registry),
        ("Schema Loading", test_schema_loading),
        ("Name Extraction", test_name_extraction),
        ("Validation", test_validation)
    ]
    
    passed = 0
    failed = 0
    
    for name, test_func in tests:
        try:
            result = test_func()
            if result:
                passed += 1
            else:
                failed += 1
                print(f"\n✗ {name} test failed")
        except Exception as e:
            failed += 1
            print(f"\n✗ {name} test failed with exception: {e}")
            import traceback
            traceback.print_exc()
    
    print("\n" + "=" * 60)
    print(f"Results: {passed} passed, {failed} failed")
    print("=" * 60)
    
    if failed == 0:
        print("\n✓ All tests passed!")
        return 0
    else:
        print(f"\n✗ {failed} test(s) failed")
        return 1


if __name__ == "__main__":
    sys.exit(main())

