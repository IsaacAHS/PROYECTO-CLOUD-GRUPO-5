#!/bin/bash

nimbuscore_ensure_ovs_ready() {
    local ovs_name="$1"
    local ovs_uplinks="${2:-}"
    local log_prefix="${3:-[ovs]}"
    local move_uplink_ips="${NIMBUSCORE_OVS_MOVE_UPLINK_IPS:-true}"

    sudo ovs-vsctl --may-exist add-br "$ovs_name"
    sudo ip link set "$ovs_name" up

    for iface in $ovs_uplinks; do
        if ! ip link show "$iface" >/dev/null 2>&1; then
            echo "$log_prefix WARN: uplink OVS no existe en este host: $iface"
            continue
        fi

        local ipv4_addrs=""
        if [ "$move_uplink_ips" = "true" ]; then
            ipv4_addrs="$(ip -o -4 addr show dev "$iface" scope global 2>/dev/null | awk '{print $4}' || true)"
        fi

        sudo ip link set "$iface" up
        sudo ip link set "$ovs_name" up

        if [ -n "$ipv4_addrs" ]; then
            local addr
            for addr in $ipv4_addrs; do
                if ! ip -o -4 addr show dev "$ovs_name" | awk '{print $4}' | grep -qx "$addr"; then
                    sudo ip addr add "$addr" dev "$ovs_name" 2>/dev/null || true
                fi
            done
        fi

        sudo ovs-vsctl --may-exist add-port "$ovs_name" "$iface"

        if [ -n "$ipv4_addrs" ]; then
            local addr ip_no_prefix
            for addr in $ipv4_addrs; do
                sudo ip addr del "$addr" dev "$iface" 2>/dev/null || true
                sudo ip addr replace "$addr" dev "$ovs_name"
                ip_no_prefix="${addr%/*}"
                local route_line
                route_line="$(ip route show dev "$ovs_name" scope link src "$ip_no_prefix" 2>/dev/null | head -n 1 || true)"
                if [ -n "$route_line" ]; then
                    sudo ip route replace $route_line 2>/dev/null || true
                fi
                echo "$log_prefix IP $addr movida de $iface a $ovs_name"
            done
        fi
    done
}
