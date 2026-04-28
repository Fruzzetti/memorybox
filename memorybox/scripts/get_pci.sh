#!/bin/bash
lspci -nn | grep -i "ethernet\|network"
lspci -tv
