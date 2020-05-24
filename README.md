[![Language grade: Python](https://img.shields.io/lgtm/grade/python/g/cisocrgroup/ocrd_cis.svg?logo=lgtm&logoWidth=18)](https://lgtm.com/projects/g/cisocrgroup/ocrd_cis/context:python)
[![Total alerts](https://img.shields.io/lgtm/alerts/g/cisocrgroup/ocrd_cis.svg?logo=lgtm&logoWidth=18)](https://lgtm.com/projects/g/cisocrgroup/ocrd_cis/alerts/)
# ocrd_cis

[CIS](http://www.cis.lmu.de) [OCR-D](http://ocr-d.de) command line
tools for the automatic post-correction of OCR-results.

## Introduction
`ocrd_cis` contains different tools for the automatic post-correction
of OCR results.  It contains tools for the training, evaluation and
execution of the post-correction.  Most of the tools are following the
[OCR-D CLI conventions](https://ocr-d.de/en/spec/cli).

Additionally, there is a helper tool to align multiple OCR results,
as well as an improved version of [Ocropy](https://github.com/tmbarchive/ocropy)
that works with Python 3 and is also wrapped for [OCR-D](https://ocr-d.de/en/spec/).

## Installation
There are 2 ways to install the `ocrd_cis` tools:
 * normal packaging:
  ```sh
  make install # or equally: pip install -U pip .
  ```
  (Installs `ocrd_cis` including its Python dependencies
   from the current directory to the Python package directory.)
 * editable mode:
  ```sh
  make install-devel # or equally: pip install -e -U pip .
  ```
  (Installs `ocrd_cis` including its Python dependencies
   from the current directory.)
 
It is possible (and recommended) to install `ocrd_cis` in a custom user directory
(instead of system-wide) by using `virtualenv` (or `venv`):
```sh
 # create venv:
 python3 -m venv venv-dir # where "venv-dir" could be any path name
 # enter venv in current shell:
 source venv-dir/bin/activate
 # install ocrd_cis:
 make install # or any other way (see above)
 # use ocrd_cis:
 ocrd-cis-ocropy-binarize ...
 # finally, leave venv:
 deactivate
```

## Profiler
The post correction is dependent on the language
[profiler](https://github.com/cisocrgroup/Profiler) and its laguage
configurations to generate corrections for suspicious words.  In order
to use the post correction a profiler with according language
configruations have to be present on the system.  You can refer to our
[manuals](https://github.com/cisocrgroup/Resources/tree/master/manuals)
and our [lexical
resources](https://github.com/cisocrgroup/Resources/tree/master/lexica)
for more information.

If you use docker you can use the preinstalled profiler from within
the docker-container.  The profiler is installed to `/apps/profiler`
and the language configurations lie in `/etc/profiler/languages` in
the container image.

## Usage
Most tools follow the [OCR-D specifications](https://ocr-d.de/en/spec),
(which makes them [OCR-D _processors_](https://ocr-d.de/en/spec/cli),)
i.e. they accept the command-line options `--input-file-grp`, `--output-file-grp`,
`--page-id`, `--parameter`, `--mets`, `--log-level` (each with an argument).
Invoke with `--help` to get self-documentation. 

Some of the processors (most notably the alignment tool) expect a comma-seperated list
of multiple input file groups, or multiple output file groups.

The [ocrd-tool.json](ocrd_cis/ocrd-tool.json) contains a formal
description of all the processors along with the parameter config file
accepted by their `--parameter` argument.

### ocrd-cis-postcorrect
This processor runs the post correction using a pre-trained model.  If
additional support OCRs should be used, models for these OCR steps are
required and must be executed and aligned beforehand (see [the test
script](tests/run_postcorrection_test.bash) for an example).

Arguments:
 * `--parameter` path to configuration file
 * `--input-file-grp` name of the master-OCR file group
 * `--output-file-grp` name of the post-correction file group
 * `--log-level` set log level
 * `--mets` path to METS file in workspace

As mentioned above in order to use the postcorrection with input from
multiple OCR's, some preprocessing steps are needed: firstly the
additional OCR recognition has to be done and secondly the multiple
OCR's have to be aligned (you can also take a look to the function
`ocrd_cis_align` in the [tests](tests/test_lib.bash)).  Assuming an
original recognition as file group `OCR1` on the segmented document of
file group `SEG`, the folloing commands can be used:

```sh
ocrd-ocropus-recognize -I SEG -O OCR2 ... # additional OCR
ocrd-cis-align -I OCR1,OCR2 -O ALGN ... # align OCR1 and OCR2
ocrd-cis-postcorrect -I ALGN -O PC ... # post correction
```

### ocrd-cis-align
Aligns tokens of multiple input file groups to one output file group.
This processor is used to align the master OCR with any additional support
OCRs.  It accepts a comma-separated list of input file groups, which
it aligns in order.

Arguments:
 * `--parameter` path to configuration file
 * `--input-file-grp` comma seperated list of the input file groups;
   first input file group is the master OCR; if there is a ground
   truth (for evaluation) it must be the last file group in the list
 * `--output-file-grp` name of the file group for the aligned result
 * `--log-level` set log level
 * `--mets` path to METS file in workspace

### ocrd-cis-data
Helper tool to get the path of the installed data files. Usage:
`ocrd-cis-data [-h|-jar|-3gs|-model|-config]` to get the path of the
jar library, the pre-trained post correction model, the path to the
default 3-grams language model file or the default training
configuration file.  This tool does not follow the OCR-D conventions.

### Trainining
There is no dedicated training script provided. Models are trained
using the java implementation directly (check out the [training test
script](tests/run_training_test.bash) for an example).  Training a
model requires a workspace containing one or more file groups
consisting of aligned OCR and ground-truth documents (the last file
group has to be the ground truth).

Arguments:
 * `--parameter` path to configuration file
 * `--input-file-grp` name of the input file group to profile
 * `--output-file-grp` name of the output file group where the profile
   is stored
 * `--log-level` set log level
 * `--mets` path to METS file in the workspace

### ocrd-cis-ocropy-train
The `ocropy-train` tool can be used to train LSTM models.
It takes ground truth from the workspace and saves (image+text) snippets from the corresponding pages.
Then a model is trained on all snippets for 1 million (or the given number of) randomized iterations from the parameter file.

```sh
java -jar $(ocrd-cis-data -jar) \
	 -c train \
	 --input-file-grp OCR1,OCR2,GT \
     --log-level DEBUG \
	 -m mets.xml \
	 --parameter $(ocrd-cis-data -config)
```

### ocrd-cis-ocropy-clip
The `clip` processor can be used to remove intrusions of neighbouring segments in regions / lines of a page.
It runs a connected component analysis on every text region / line of every PAGE in the input file group, as well as its overlapping neighbours, and for each binary object of conflict, determines whether it belongs to the neighbour, and can therefore be clipped to the background. It references the resulting segment image files in the output PAGE (via `AlternativeImage`).
(Use this to suppress separators and neighbouring text.)
```sh
ocrd-cis-ocropy-clip \
  --input-file-grp OCR-D-SEG-REGION \
  --output-file-grp OCR-D-SEG-REGION-CLIP \
  --mets mets.xml
  --parameter path/to/config.json
```

### ocrd-cis-ocropy-resegment
The `resegment` processor can be used to remove overlap between neighbouring lines of a page.
It runs a line segmentation on every text region of every PAGE in the input file group, and for each line already annotated, determines the label of largest extent within the original coordinates (polygon outline) in that line, and annotates the resulting coordinates in the output PAGE.
(Use this to polygonalise text lines that are poorly segmented, e.g. via bounding boxes.)
```sh
ocrd-cis-ocropy-resegment \
  --input-file-grp OCR-D-SEG-LINE \
  --output-file-grp OCR-D-SEG-LINE-RES \
  --mets mets.xml
  --parameter path/to/config.json
```

### ocrd-cis-ocropy-segment
The `segment` processor can be used to segment (pages or) regions of a page into (regions and) lines.
It runs a line segmentation on every (page or) text region of every PAGE in the input file group, and adds (text regions containing) `TextLine` elements with the resulting polygon outlines to the annotation of the output PAGE.
(Does _not_ detect tables.)
```sh
ocrd-cis-ocropy-segment \
  --input-file-grp OCR-D-SEG-BLOCK \
  --output-file-grp OCR-D-SEG-LINE \
  --mets mets.xml
  --parameter path/to/config.json
```

### ocrd-cis-ocropy-deskew
The `deskew` processor can be used to deskew pages / regions of a page.
It runs a projection profile-based skew estimation on every segment of every PAGE in the input file group and annotates the orientation angle in the output PAGE.
(Does _not_ include orientation detection.)
```sh
ocrd-cis-ocropy-deskew \
  --input-file-grp OCR-D-SEG-LINE \
  --output-file-grp OCR-D-SEG-LINE-DES \
  --mets mets.xml
  --parameter path/to/config.json
```

### ocrd-cis-ocropy-denoise
The `denoise` processor can be used to despeckle pages / regions / lines of a page.
It runs a connected component analysis and removes small components (black or white) on every segment of every PAGE in the input file group and references the resulting segment image files in the output PAGE (as `AlternativeImage`).
```sh
ocrd-cis-ocropy-denoise \
  --input-file-grp OCR-D-SEG-LINE-DES \
  --output-file-grp OCR-D-SEG-LINE-DEN \
  --mets mets.xml
  --parameter path/to/config.json
```

### ocrd-cis-ocropy-binarize
The `binarize` processor can be used to binarize (and optionally denoise and deskew) pages / regions / lines of a page.
It runs the "nlbin" adaptive whitelevel thresholding on every segment of every PAGE in the input file group and references the resulting segment image files in the output PAGE (as `AlternativeImage`). (If a deskewing angle has already been annotated in a region, the tool respects that and rotates accordingly.) Images can also be produced grayscale-normalized.
```sh
ocrd-cis-ocropy-binarize \
  --input-file-grp OCR-D-SEG-LINE-DES \
  --output-file-grp OCR-D-SEG-LINE-BIN \
  --mets mets.xml
  --parameter path/to/config.json
```

### ocrd-cis-ocropy-dewarp
The `dewarp` processor can be used to vertically dewarp text lines of a page.
It runs the baseline estimation and center normalizer algorithm on every line in every text region of every PAGE in the input file group and references the resulting line image files in the output PAGE (as `AlternativeImage`).
```sh
ocrd-cis-ocropy-dewarp \
  --input-file-grp OCR-D-SEG-LINE-BIN \
  --output-file-grp OCR-D-SEG-LINE-DEW \
  --mets mets.xml
  --parameter path/to/config.json
```

### ocrd-cis-ocropy-recognize
The `recognize` processor can be used to recognize the lines / words / glyphs of a page.
It runs LSTM optical character recognition on every line in every text region of every PAGE in the input file group and adds the resulting text annotation in the output PAGE.
```sh
ocrd-cis-ocropy-recognize \
  --input-file-grp OCR-D-SEG-LINE-DEW \
  --output-file-grp OCR-D-OCR-OCRO \
  --mets mets.xml
  --parameter path/to/config.json
```

### Tesserocr
Install essential system packages for Tesserocr
```sh
sudo apt-get install python3-tk \
  tesseract-ocr libtesseract-dev libleptonica-dev \
  libimage-exiftool-perl libxml2-utils
```

Then install Tesserocr from: https://github.com/OCR-D/ocrd_tesserocr
```sh
pip install -r requirements.txt
pip install .
```

Download and move tesseract models from:
https://github.com/tesseract-ocr/tesseract/wiki/Data-Files or use your
own models and place them into: /usr/share/tesseract-ocr/4.00/tessdata

## Workflow configuration

A decent pipeline might look like this:

1. image normalization/optimization
1. page-level binarization
1. page-level cropping
1. (page-level binarization)
1. (page-level despeckling)
1. page-level deskewing
1. (page-level dewarping)
1. region segmentation, possibly subdivided into
   1. text/non-text separation
   1. text region segmentation (and classification)
   1. reading order detection
   1. non-text region classification
1. region-level clipping
1. (region-level deskewing)
1. line segmentation
1. (line-level clipping or resegmentation)
1. line-level dewarping
1. line-level recognition
1. (line-level alignment and post-correction)

If GT is used, then cropping/segmentation steps can be omitted.

If a segmentation is used which does not produce overlapping segments, then clipping/resegmentation can be omitted.

## Testing
To run a few basic tests type `make test` (`ocrd_cis` has to be
installed in order to run any tests).

# Miscellaneous
## OCR-D workspace

* Create a new (empty) workspace: `ocrd workspace init workspace-dir`
* cd into `workspace-dir`
* Add new file to workspace: `ocrd workspace add file -G group -i id
  -m mimetype -g pageId`

## OCR-D links

- [OCR-D](https://ocr-d.github.io)
- [Github](https://github.com/OCR-D)
- [Project-page](http://www.ocr-d.de/)
- [Ground-truth](https://ocr-d-repo.scc.kit.edu/api/v1/metastore/bagit/search)
