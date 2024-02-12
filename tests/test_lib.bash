#/bin/bash

tmpdir=$(mktemp -d)
trap "rm -rf $tmpdir" EXIT

OCRD_CIS_FILEGRP="OCR-D-GT-SEG-LINE"
# fixme: it does not work like this - the OCR-D GT repo uses different URL paths for different datasets
# this is merely the path for blumenbach_anatomie_1805.ocrd.zip
data_url="https://ocr-d-repo.scc.kit.edu/api/v1/dataresources/75ad9f94-dbaa-43e0-ab06-2ce24c497c61/data/"
function ocrd_cis_download_bagit() {
	local url="$data_url/$1"
	mkdir -p "$tmpdir/download"
	wget -nc -P "$tmpdir/download" "$url"
}

function ocrd_cis_init_ws() {
	ocrd_cis_download_bagit "$1"
	ocrd zip spill -d "$tmpdir" "$tmpdir/download/$1"
	tmpws="$tmpdir/${1%.ocrd.zip}"
}

function ocrd_cis_align() {
	# download ocr models
	ocrd resmgr download ocrd-cis-ocropy-recognize fraktur.pyrnn.gz
	ocrd resmgr download ocrd-cis-ocropy-recognize fraktur-jze.pyrnn.gz
	# run ocr
	ocrd-cis-ocropy-recognize -l DEBUG -m $tmpws/mets.xml \
 				-I $OCRD_CIS_FILEGRP -O OCR-D-CIS-OCR-1 \
				-P textequiv_level word -P model fraktur.pyrnn.gz
	ocrd-cis-ocropy-recognize -l DEBUG -m $tmpws/mets.xml \
				-I $OCRD_CIS_FILEGRP -O OCR-D-CIS-OCR-2 \
				-P textequiv_level word -P model fraktur-jze.pyrnn.gz
	ocrd-cis-align -l DEBUG -m $tmpws/mets.xml \
				-I OCR-D-CIS-OCR-1,OCR-D-CIS-OCR-2,$OCRD_CIS_FILEGRP \
				-O OCR-D-CIS-ALIGN 
}
