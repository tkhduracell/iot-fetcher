#!/bin/bash

if [ "$#" -lt 1 ]; then
    echo "Usage: $0 <fleet_name>"
    exit 1
fi

FLEET_NAME="$1"

while IFS= read -r line || [ -n "$line" ]; do
    if [ -n "$line" ]; then
        IFS='=' read -r var value <<< "$line"
        # if it starts with a comment or is empty, skip it
        if [[ "$var" == \#* ]] || [ -z "$var" ]; then
            continue
        fi
        echo "balena env set --fleet "$FLEET_NAME" --service iot-fetcher $var <...>"
        balena env set --fleet "$FLEET_NAME" --service iot-fetcher $var ${value//[\`\'\"]/}
    fi
done < "fetcher-core/.env"

while IFS= read -r line || [ -n "$line" ]; do
    if [ -n "$line" ]; then
        IFS='=' read -r var value <<< "$line"
        # if it starts with a comment or is empty, skip it
        if [[ "$var" == \#* ]] || [ -z "$var" ]; then
            continue
        fi
        echo "balena env set --fleet "$FLEET_NAME" --service https-proxy "$var" <...>"
        balena env set --fleet "$FLEET_NAME" --service https-proxy "$var" "$value"
    fi
done < "https-proxy/.env"

while IFS= read -r line || [ -n "$line" ]; do
    if [ -n "$line" ]; then
        IFS='=' read -r var value <<< "$line"
        # if it starts with a comment or is empty, skip it
        if [[ "$var" == \#* ]] || [ -z "$var" ]; then
            continue
        fi
        echo "balena env set --fleet "$FLEET_NAME" --service garmin-influx-bridge $var <...>"
        balena env set --fleet "$FLEET_NAME" --service garmin-influx-bridge $var ${value//[\`\'\"]/}
    fi
done < "garmin-influx-bridge/.env"

while IFS= read -r line || [ -n "$line" ]; do
    if [ -n "$line" ]; then
        IFS='=' read -r var value <<< "$line"
        # if it starts with a comment or is empty, skip it
        if [[ "$var" == \#* ]] || [ -z "$var" ]; then
            continue
        fi
        echo "balena env set --fleet "$FLEET_NAME" --service tibber-influx-bridge $var <...>"
        balena env set --fleet "$FLEET_NAME" --service tibber-influx-bridge $var ${value//[\`\'\"]/}
    fi
done < "tibber-influx-bridge/.env"
