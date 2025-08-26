# tests/run_tests.py
"""
Test runner for Py2Flamingo unit tests.

This script runs all unit tests and provides a summary of results.
"""
import unittest
import sys
import os
from pathlib import Path

repo_root = Path(__file__).parent.parent
sys.path.insert(0, str(repo_root))           # so 'src' is a package (for patch paths)
sys.path.insert(0, str(repo_root / 'src'))

# Add src directory to path
src_path = Path(__file__).parent.parent / 'src'
sys.path.insert(0, str(src_path))


def run_all_tests():
    """Run all unit tests and return results."""
    # Discover all tests
    loader = unittest.TestLoader()
    start_dir = Path(__file__).parent
    suite = loader.discover(start_dir, pattern='test_*.py')
    
    # Run tests
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    
    return result.wasSuccessful()


def run_specific_test(test_module):
    """Run a specific test module."""
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromName(test_module)
    
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    
    return result.wasSuccessful()


if __name__ == '__main__':
    print("=" * 70)
    print("Py2Flamingo Unit Test Suite")
    print("=" * 70)
    
    if len(sys.argv) > 1:
        # Run specific test
        test_name = sys.argv[1]
        print(f"\nRunning specific test: {test_name}")
        success = run_specific_test(f'test_{test_name}')
    else:
        # Run all tests
        print("\nRunning all tests...")
        success = run_all_tests()
    
    print("\n" + "=" * 70)
    if success:
        print("All tests passed!")
        sys.exit(0)
    else:
        print("Some tests failed!")
        sys.exit(1)
