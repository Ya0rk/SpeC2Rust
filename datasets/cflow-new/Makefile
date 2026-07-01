CFLAG?=-g -O0
SHELL = /bin/bash

.PHONY: all clean cflow am--refresh

all: cflow

cflow:
	@if [ ! -f config.status ] || [ ! -f src/Makefile ] || [ ! -f gnu/Makefile ]; then \
		echo "Running configure..."; \
		./configure --quiet --disable-nls; \
	fi
	@$(MAKE) -C gnu
	@$(MAKE) -C src cflow
	@cp -f src/cflow .
	@echo "cflow built successfully."

am--refresh:
	@:

clean:
	rm -f cflow
	-$(MAKE) -C src clean
	-$(MAKE) -C gnu clean
