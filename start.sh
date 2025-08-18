#!/bin/bash

# if arguments are passed, use them as parameters for the main script
# if the first argument if -- remove it

if [ "$1" = "--" ]; then
    shift
fi

# If argument is set
if [ -n "$1" ]; then
    echo "Arguments passed to the script: $@"

    # the arg is webui
    if [ "$1" = "webui" ]; then
        python3 ./webui/web.py "$@"
        exit 0
    fi

    python3 ./python/src/main.py "$@"
    exit 0
fi

python3 ./webui/web.py &

python3 ./python/src/main.py &

node ./nodejs/dist/index.js &

# Wait for all background processes to finish
wait

# Exit with status 0 after all processes finish
exit 0