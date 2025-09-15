#!/usr/bin/env python3

"""
Manual test script for TAPO integration.

This script can be used to test the TAPO integration with real credentials.
Set the environment variables TAPO_EMAIL and TAPO_PASSWORD before running.

Usage:
    export TAPO_EMAIL="your-email@example.com"  
    export TAPO_PASSWORD="your-password"
    python3 test_tapo_integration.py

Or run with Docker:
    docker run --rm --env-file .env iot-fetcher:latest -- tapo
"""

import os
import sys
import logging

# Configure logging to be more verbose for testing
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

# Add the src directory to the path
sys.path.insert(0, '/home/runner/work/iot-fetcher/iot-fetcher/python/src')

def main():
    """Test TAPO integration with real credentials."""
    
    # Check for required environment variables
    tapo_email = os.environ.get('TAPO_EMAIL', '')
    tapo_password = os.environ.get('TAPO_PASSWORD', '')
    
    if not tapo_email or not tapo_password:
        print("❌ Please set TAPO_EMAIL and TAPO_PASSWORD environment variables")
        print("\nUsage:")
        print("  export TAPO_EMAIL='your-email@example.com'")  
        print("  export TAPO_PASSWORD='your-password'")
        print("  python3 test_tapo_integration.py")
        print("\nOr run in Docker container:")
        print("  docker run --rm --env-file .env iot-fetcher:latest -- tapo")
        sys.exit(1)
    
    print("🔧 Testing TAPO integration...")
    print(f"📧 Email: {tapo_email}")
    print("🔑 Password: [SET]")
    
    # Test import
    try:
        import tapo
        print("✅ TAPO module imported successfully")
    except ImportError as e:
        print(f"❌ Failed to import TAPO module: {e}")
        print("💡 This is expected when running outside the Docker container")
        print("   The plugp100 dependency needs to be installed")
        sys.exit(1)
    
    # Test running the function
    try:
        print("🚀 Running TAPO data collection...")
        tapo.tapo()
        print("✅ TAPO integration completed successfully!")
        
    except Exception as e:
        print(f"❌ TAPO integration failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == '__main__':
    main()