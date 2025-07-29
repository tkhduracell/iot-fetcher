#!/bin/bash

if [ "$#" -lt 1 ]; then
    echo "Usage: $0 <fleet_name> <device>"
    exit 1
fi

FLEET_NAME="$1"
DEVICE="$2"

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
        echo "balena env set --fleet "$FLEET_NAME" --service database $var <...>"
        balena env set --fleet "$FLEET_NAME" --service database $var ${value//[\`\'\"]/}
    fi
done < "influx-2/.env"

while IFS= read -r line || [ -n "$line" ]; do
    if [ -n "$line" ]; then
        IFS='=' read -r var value <<< "$line"
        # if it starts with a comment or is empty, skip it
        if [[ "$var" == \#* ]] || [ -z "$var" ]; then
            continue
        fi
        echo "balena env set --fleet "$FLEET_NAME" --service influx-3 $var <...>"
        balena env set --fleet "$FLEET_NAME" --service influx-3 $var ${value//[\`\'\"]/}
    fi
done < "influx-3/.env"

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
done < ".env"

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
