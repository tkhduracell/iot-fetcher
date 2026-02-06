#!/usr/bin/env python3
"""
Quick test to find the right broadcast address for TAPO discovery
"""

import asyncio
import sys
from plugp100.discovery.tapo_discovery import TapoDiscovery

async def test_broadcast(broadcast_addr):
    print(f"Testing broadcast address: {broadcast_addr}")
    try:
        devices = await TapoDiscovery.scan(timeout=3, broadcast=broadcast_addr)
        print(f"✓ Success! Found {len(devices)} device(s)")
        for dev in devices:
            print(f"  - {dev.device_model} at {dev.ip}")
        return True
    except Exception as e:
        print(f"✗ Failed: {e}")
        return False

async def main():
    # Test different broadcast addresses
    broadcasts = [
        "192.168.71.255",      # Your subnet broadcast (192.168.68.0/22)
        "192.168.68.255",      # /24 within your range
        "192.168.69.255",      # /24 within your range
        "192.168.70.255",      # /24 within your range
        "255.255.255.255",     # Default broadcast
    ]

    print("Testing different broadcast addresses...\n")

    for broadcast in broadcasts:
        if await test_broadcast(broadcast):
            print(f"\n✓ Working broadcast address: {broadcast}")
            break
        print()
    else:
        print("\n⚠️  No working broadcast address found!")
        print("This may mean:")
        print("  1. No TAPO devices on this network")
        print("  2. Firewall blocking UDP port 20002")
        print("  3. Devices are on a different network segment")

if __name__ == "__main__":
    asyncio.run(main())
