#!/bin/bash

# if arguments are passed, use them as parameters for the main script
if [ $# -gt 0 ]; then
    echo "Arguments passed to the script: $@"
    python3 ./python/src/main.py $@
    exit 0
fi

python3 ./webui/web.py &

python3 ./python/src/main.py &

npm --prefix nodejs run start &

# Wait for all background processes to finish
wait

# Exit with status 0 after all processes finish
exit 0