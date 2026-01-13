sudo pkill -f ydotoold || true
sudo ydotoold -p /tmp/.ydotool_socket -P 0660 -o "$(id -u):$(id -g)" >/tmp/ydotoold.log 2>&1 &

source ~/venvs/data-science/bin/activate && sudo env "PATH=$PATH" python main.py --global --device /dev/input/event3

sudo pkill -f ydotoold