#!/bin/bash
# bridge_sim.sh - Simulated Network Bridge Toggle
# This script uses Network Namespaces to test bridging logic without affecting the host NIC.

NAMESPACE="concierge_sim"
V_ETH_HOST="veth_host"
V_ETH_SIM="veth_sim"
BRIDGE="vbr0"

setup_sim() {
    echo "[*] Setting up simulation namespace: $NAMESPACE"
    ip netns add $NAMESPACE
    
    # Create veth pair
    ip link add $V_ETH_HOST type veth peer name $V_ETH_SIM
    
    # Move one end to namespace
    ip link set $V_ETH_SIM netns $NAMESPACE
    
    # Create Bridge on host
    ip link add $BRIDGE type bridge
    ip link set $V_ETH_HOST master $BRIDGE
    
    # Start links
    ip link set $V_ETH_HOST up
    ip link set $BRIDGE up
    ip netns exec $NAMESPACE ip link set lo up
    ip netns exec $NAMESPACE ip link set $V_ETH_SIM up
    
    echo "[+] Simulation environment ready."
}

test_private() {
    echo "[*] SIMULATING PRIVATE MODE (Concierge DHCP)"
    # Static IP in namespace
    ip netns exec $NAMESPACE ip addr add 172.16.0.2/24 dev $V_ETH_SIM
    echo "[+] Namespace simulates device at 172.16.0.2"
}

test_bridge() {
    echo "[*] SIMULATING BRIDGED MODE (WAN Pass-through)"
    # Remove static
    ip netns exec $NAMESPACE ip addr flush dev $V_ETH_SIM
    # Simulate DHCP request (requires a dhcp server on the host bridge, like dnsmasq)
    echo "[+] Namespace ready for WAN DHCP."
}

cleanup() {
    echo "[*] Cleaning up simulation..."
    ip netns del $NAMESPACE 2>/dev/null
    ip link del $V_ETH_HOST 2>/dev/null
    ip link del $BRIDGE 2>/dev/null
    echo "[+] Cleanup complete."
}

case "$1" in
    setup) setup_sim ;;
    private) test_private ;;
    bridge) test_bridge ;;
    cleanup) cleanup ;;
    *) echo "Usage: $0 {setup|private|bridge|cleanup}" ;;
esac
