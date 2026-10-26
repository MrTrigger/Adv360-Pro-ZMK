TIMESTAMP ?= $(shell date -u +"%Y%m%d%H%M")
COMMIT ?= $(shell git rev-parse --short HEAD)

.PHONY: all left legacy clean_firmware clean

all:
	TIMESTAMP=$(TIMESTAMP) COMMIT=$(COMMIT) scripts/build-firmware --flavor studio

left:
	TIMESTAMP=$(TIMESTAMP) COMMIT=$(COMMIT) scripts/build-firmware --flavor studio --side left

legacy:
	TIMESTAMP=$(TIMESTAMP) COMMIT=$(COMMIT) scripts/build-firmware --flavor legacy

clean_firmware:
	rm -f firmware/*.uf2

clean: clean_firmware
