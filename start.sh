#!/bin/bash

python3 ./webui/web.py &

python3 ./python/src/main.py &

npm --prefix nodejs run start &

# Wait for all background processes to finish
wait

# Exit with status 0 after all processes finish
exit 0