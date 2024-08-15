from __future__ import absolute_import
from logging import Logger
from os.path import join
import numpy as np
from skimage import draw, segmentation
from shapely.geometry import Polygon, LineString
from shapely.prepared import prep

from ocrd_modelfactory import page_from_file
from ocrd_models.ocrd_page import BaselineType, PageType, to_xml
from ocrd import Processor
from ocrd_utils import (
    getLogger,
    make_file_id,
    coordinates_of_segment,
    coordinates_for_segment,
    points_from_polygon,
    polygon_from_points,
    transform_coordinates,
    MIMETYPE_PAGE
)

from .ocrolib import midrange, morph
from .common import (
    pil2array,
    odd,
    DSAVE,
    determine_zoom,
    # binarize,
    check_page,
    check_region,
    compute_segmentation
    #borderclean_bin
)
from .segment import (
    masks2polygons,
    polygon_for_parent,
    make_valid,
    make_intersection,
    join_baselines,
    join_polygons,
    diff_polygons
)

class OcropyResegment(Processor):
    logger: Logger

    @property
    def executable(self):
        return 'ocrd-cis-ocropy-resegment'

    def setup(self):
        self.logger = getLogger('processor.OcropyResegment')

    def process(self):
        """Resegment lines of the workspace.

        Open and deserialise PAGE input files and their respective images,
        then iterate over the element hierarchy down to the line level.

        Next, get the page image according to the layout annotation (from
        the alternative image of the page, or by cropping from annotated
        Border and rotating from annotated orientation).

        \b
        If ``method`` is `lineest`, then compute a new line segmentation
        for that image (suppressing the foreground non-text regions), and
        polygonalize its contours. Now calculate overlaps between the new
        and old lines, i.e. which existing polygons (or rectangles) match
        each new line polygon: Among the existing lines covering most of each
        new line's foreground and background area, assign the one with the
        largest share of the existing line. Next, for each existing line,
        calculate the concave hull polygon of all assigned new lines, and
        if the foreground and background overlap is sufficient, and if no
        overlapping but yet unassigned lines would be lost, then annotate
        that polygon as new coordinates.
        (Thus, at the end, all new and existing lines will have been used
        at most once, but not all existing lines might have been resegmented
        – either because there were no matches at all, or the loss would have
        been too large in terms of fg/bg share.)

        Otherwise, first compute a connected component analysis of that image.
        Next, if  ``method`` is `ccomps`, then calculate a distance transform
        of the existing (overlapping) line labels and flatten them by selecting
        the labels with maximum distance, respectively. Else if ``method`` is
        `baseline`, then create a flat segmentation by applying dilation on the
        top side of the baselines. Subsequently, regardless of the ``method``,
        propagate these new line seeds to the connected components, with conflicts
        taking the majority label. Spread these foreground labels into the
        background and find contour polygons for them. For each line, calculate
        the concave hull polygon of its constituent contours. If the foreground
        and background overlap is sufficent, then annotate that polygon as new
        coordinates.

        Produce a new output file by serialising the resulting hierarchy.
        """
        # This makes best sense for bad/coarse line segmentation, like current GT
        # or as postprocessing for bbox-only steps like Tesseract.
        # Most notably, it can convert rectangles to polygons (polygonalization),
        # and enforce conflicting lines shared between neighbouring regions.
        # It depends on a decent line segmentation from ocropy though. So it
        # _should_ ideally be run after deskewing (on the page level),
        # _must_ be run after binarization (on page level). Also, the method's
        # accuracy crucially depends on a good estimate of the images'
        # pixel density (at least if source input is not 300 DPI).
        level = self.parameter['level-of-operation']

        for n, input_file in enumerate(self.input_files):
            self.logger.info("INPUT FILE %i / %s", n, input_file.pageId or input_file.ID)
            file_id = make_file_id(input_file, self.output_file_grp)

            pcgts = page_from_file(self.workspace.download_file(input_file))
            self.add_metadata(pcgts)
            page_id = pcgts.pcGtsId or input_file.pageId or input_file.ID
            page = pcgts.get_Page()

            page_image, page_coords, page_image_info = self.workspace.image_from_page(
                page, page_id, feature_selector='binarized')

            zoom = determine_zoom(self.logger, page_id, self.parameter['dpi'], page_image_info)

            ignore = (page.get_ImageRegion() +
                      page.get_LineDrawingRegion() +
                      page.get_GraphicRegion() +
                      page.get_ChartRegion() +
                      page.get_MapRegion() +
                      page.get_MathsRegion() +
                      page.get_ChemRegion() +
                      page.get_MusicRegion() +
                      page.get_AdvertRegion() +
                      page.get_NoiseRegion() +
                      page.get_SeparatorRegion() +
                      page.get_UnknownRegion() +
                      page.get_CustomRegion())
            regions = page.get_AllRegions(classes=['Text'])
            if not regions:
                self.logger.warning('Page "%s" contains no text regions', page_id)
            elif level == 'page':
                lines = [line for region in regions
                         for line in region.get_TextLine()]
                if lines:
                    self._process_segment(page, page_image, page_coords, page_id, zoom, lines, ignore)
                else:
                    self.logger.warning('Page "%s" contains no text regions with lines', page_id)
            else:
                for region in regions:
                    lines = region.get_TextLine()
                    if lines:
                        region_image, region_coords = self.workspace.image_from_segment(
                            region, page_image, page_coords, feature_selector='binarized')
                        self._process_segment(region, region_image, region_coords, page_id, zoom, lines, ignore)
                    else:
                        self.logger.warning('Page "%s" region "%s" contains no text lines', page_id, region.id)

            # update METS (add the PAGE file):
            file_path = join(self.output_file_grp, file_id + '.xml')
            pcgts.set_pcGtsId(file_id)
            out = self.workspace.add_file(
                ID=file_id,
                file_grp=self.output_file_grp,
                pageId=input_file.pageId,
                local_filename=file_path,
                mimetype=MIMETYPE_PAGE,
                content=to_xml(pcgts))
            self.logger.info('created file ID: %s, file_grp: %s, path: %s',
                     file_id, self.output_file_grp, out.local_filename)

    def _process_segment(self, parent, parent_image, parent_coords, page_id, zoom, lines, ignore):
        threshold = self.parameter['min_fraction']
        method = self.parameter['method']
        maxdist = self.parameter['spread']/zoom*300/72 # in pt
        # prepare line segmentation
        parent_array = pil2array(parent_image)
        #parent_array, _ = common.binarize(parent_array, maxskew=0) # just in case still raw
        parent_bin = np.array(parent_array <= midrange(parent_array), bool)
        ignore_bin = np.ones_like(parent_bin, bool)
        if isinstance(parent, PageType):
            tag = 'page'
            fullpage = True
            report = check_page(parent_bin, zoom)
        else:
            tag = 'region'
            fullpage = False
            report = check_region(parent_bin, zoom)
        if report:
            self.logger.warning('Invalid %s "%s": %s', tag,
                        page_id if fullpage else parent.id, report)
            return
        # get existing line labels:
        line_labels = np.zeros_like(parent_bin, bool)
        line_labels = np.tile(line_labels[np.newaxis], (len(lines), 1, 1))
        line_polygons = []
        for i, line in enumerate(lines):
            if self.parameter['baseline_only'] and line.Baseline:
                line_base = baseline_of_segment(line, parent_coords)
                line_poly = polygon_from_baseline(line_base, 30/zoom)
            else:
                line_poly = coordinates_of_segment(line, parent_image, parent_coords)
                line_poly = make_valid(Polygon(line_poly))
            line_polygons.append(line_poly)
        line_polygons = list(map(prep, line_polygons))
        for i, line_polygon in enumerate(line_polygons):
            polygon = np.array(line_polygon.context.exterior.coords, int)[:-1]
            # draw.polygon: If any line_polygon lies outside of parent
            # (causing negative/above-max indices), either fully or partially,
            # then this will silently ignore them. The caller does not need
            # to concern herself with this.
            line_y, line_x = draw.polygon(polygon[:, 1],
                                          polygon[:, 0],
                                          parent_bin.shape)
            line_labels[i, line_y, line_x] = True
        # only text region(s) may contain new text lines
        for i, region in enumerate(set(line.parent_object_ for line in lines)):
            self.logger.debug('unmasking area of text region "%s" for "%s"',
                      region.id, page_id if fullpage else parent.id)
            region_polygon = coordinates_of_segment(region, parent_image, parent_coords)
            region_polygon = make_valid(Polygon(region_polygon))
            region_polygon = np.array(region_polygon.exterior.coords, int)[:-1]
            ignore_bin[draw.polygon(region_polygon[:, 1],
                                    region_polygon[:, 0],
                                    parent_bin.shape)] = False
        # mask/ignore overlapping neighbours
        for i, segment in enumerate(ignore):
            self.logger.debug('masking area of %s "%s" for "%s"', type(segment).__name__[:-4],
                      segment.id, page_id if fullpage else parent.id)
            segment_polygon = coordinates_of_segment(segment, parent_image, parent_coords)
            ignore_bin[draw.polygon(segment_polygon[:, 1],
                                    segment_polygon[:, 0],
                                    parent_bin.shape)] = True
        if method != 'lineest':
            self.logger.debug('calculating connected component and distance transforms for "%s"', parent.id)
            bin = parent_bin & ~ ignore_bin
            components, _ = morph.label(bin)
            # estimate glyph scale (roughly)
            _, counts = np.unique(components, return_counts=True)
            if counts.shape[0] > 1:
                counts = np.sqrt(3 * counts)
                scale = int(np.median(counts[(5/zoom < counts) & (counts < 100/zoom)]))
                components *= (counts > 15/zoom)[components]
                self.logger.debug("estimated scale: %d", scale)
            else:
                scale = 43
            if method == 'ccomps':
                labels = np.insert(line_labels, 0, ignore_bin, axis=0)
                distances = np.zeros_like(labels, np.uint8)
                for i, label in enumerate(labels):
                    distances[i] = morph.dist_labels(label.astype(np.uint8))
                    # normalize the distances of all lines so larger ones do not displace smaller ones
                    if distances[i].any():
                        distances[i] = distances[i] / distances[i].max() * 255
                # use depth to flatten overlapping lines as seed labels
                new_labels = np.argmax(distances, axis=0)
            else:
                # 'baseline'
                new_labels = np.zeros_like(parent_bin, np.uint8)
                for i, line in enumerate(lines):
                    if line.Baseline is None:
                        self.logger.warning("Skipping '%s' without baseline", line.id)
                        new_labels[line_labels[i]] = i + 1
                        continue
                    line_baseline = baseline_of_segment(line, parent_coords)
                    line_polygon = polygon_from_baseline(line_baseline, maxdist or scale/2)
                    line_polygon = np.array(line_polygon.exterior.coords, int)[:-1]
                    line_y, line_x = draw.polygon(line_polygon[:, 1],
                                                  line_polygon[:, 0],
                                                  parent_bin.shape)
                    new_labels[line_y, line_x] = i + 1
            spread_dist(self.logger, lines, line_labels, new_labels, parent_bin, components, parent_coords,
                        maxdist=maxdist or scale/2, loc=parent.id, threshold=threshold)
            return
        try:
            # TODO: 'scale' passed as a param may not be always defined (mehmedGIT)
            new_line_labels, new_baselines, _, _, _, scale = compute_segmentation(
                parent_bin, seps=ignore_bin, zoom=zoom, spread_dist=maxdist or scale/2,
                fullpage=fullpage, maxseps=0, maxcolseps=len(ignore), maximages=0)
        except Exception as err:
            self.logger.error('Cannot line-segment %s "%s": %s',
                      tag, page_id if fullpage else parent.id, err)
            return
        self.logger.info("Found %d new line labels for %d existing lines on %s '%s'",
                 new_line_labels.max(), len(lines), tag, parent.id)
        # polygonalize and prepare comparison
        new_line_polygons, new_line_labels = masks2polygons(self.logger,
            new_line_labels, new_baselines, parent_bin, '%s "%s"' % (tag, parent.id),
            min_area=640/zoom/zoom)
        DSAVE('line_labels', [np.argmax(np.insert(line_labels, 0, 0, axis=0), axis=0), parent_bin])
        DSAVE('new_line_labels', [new_line_labels, parent_bin])
        new_line_polygons, new_baselines = list(zip(*[(Polygon(poly), LineString(base))
                                                      for _, poly, base in new_line_polygons])) or ([], [])
        # polygons for intersecting pairs
        intersections = dict()
        # ratio of overlap between intersection and new line
        fits_bg = np.zeros((len(new_line_polygons), len(line_polygons)), float)
        fits_fg = np.zeros((len(new_line_polygons), len(line_polygons)), float)
        # ratio of overlap between intersection and existing line
        covers_bg = np.zeros((len(new_line_polygons), len(line_polygons)), float)
        covers_fg = np.zeros((len(new_line_polygons), len(line_polygons)), float)
        # compare segmentations, calculating ratios of overlapping fore/background area
        for i, new_line_poly in enumerate(new_line_polygons):
            for j, line_poly in enumerate(line_polygons):
                # too strict: .contains
                if not line_poly.intersects(new_line_poly):
                    continue
                inter = make_intersection(line_poly.context, new_line_poly)
                if not inter:
                    continue
                new_line_mask = (new_line_labels == i+1) & parent_bin
                line_mask = line_labels[j] & parent_bin
                inter_mask = new_line_mask & line_mask
                if (not np.count_nonzero(inter_mask) or
                    not np.count_nonzero(new_line_mask) or
                    not np.count_nonzero(line_mask)):
                    continue
                intersections[(i, j)] = inter
                fits_bg[i, j] = inter.area / new_line_poly.area
                covers_bg[i, j] = inter.area / line_poly.context.area
                fits_fg[i, j] = np.count_nonzero(inter_mask) / np.count_nonzero(new_line_mask)
                covers_fg[i, j] = np.count_nonzero(inter_mask) / np.count_nonzero(line_mask)
                # LOG.debug("new %d old %d (%s): %.1f%% / %.1f%% bg, %.1f%% / %.1f%% fg",
                #           i, j, lines[j].id,
                #           fits_bg[i,j]*100, covers_bg[i,j]*100,
                #           fits_fg[i,j]*100, covers_fg[i,j]*100)
        # assign existing lines to new lines (1:n), if possible
        # start from best matches (forced alignment)
        dim1 = len(new_line_polygons)
        dim2 = len(line_polygons)
        idx1 = np.arange(dim1)
        idx2 = np.arange(dim2)
        keep1 = np.ones(dim1, bool)
        keep2 = np.ones(dim2, bool)
        assignments = -1 * np.ones(dim1, int)
        for _ in range(dim1):
            fit_bg_view = fits_bg[np.ix_(keep1, keep2)]
            if not fit_bg_view.size:
                break
            cov_bg_view = covers_bg[np.ix_(keep1, keep2)]
            fit_fg_view = fits_fg[np.ix_(keep1, keep2)]
            cov_fg_view = covers_fg[np.ix_(keep1, keep2)]
            priority = cov_fg_view * cov_bg_view
            ind1, ind2 = np.unravel_index(np.argmax(priority, axis=None), priority.shape)
            fit_fg = fit_fg_view[ind1, ind2]
            fit_bg = fit_bg_view[ind1, ind2]
            cov_fg = cov_fg_view[ind1, ind2]
            cov_bg = cov_bg_view[ind1, ind2]
            # return to full view and assign next
            ind1 = idx1[keep1][ind1]
            ind2 = idx2[keep2][ind2]
            #new_poly = new_line_polygons[ind1]
            #poly = line_polygons[ind2]
            # assignment must be new
            assert assignments[ind1] < 0
            assert keep1[ind1]
            assert keep2[ind2]
            # minimum threshold
            if not (fit_bg > 0.6 and fit_fg > 0.7):
                # skip next time
                # LOG.debug("match for %s too large: %d%%fg / %d%%bg", lines[ind2].id, fit_fg*100, fit_bg*100)
                covers_bg[ind1, ind2] = 0
                covers_fg[ind1, ind2] = 0
                continue
            assignments[ind1] = ind2
            keep1[ind1] = False
            #keep2[ind2] = False
        # validate assignments retain enough area and do not loose unassigned matches
        for j, line in enumerate(lines):
            new_lines = np.nonzero(assignments == j)[0]
            if not np.prod(new_lines.shape):
                self.logger.debug("no lines for '%s' match or fit", line.id)
                continue
            covers = np.sum(covers_bg[new_lines,j])
            if covers < threshold / 3:
                self.logger.debug("new lines for '%s' only cover %.1f%% bg",
                          line.id, covers * 100)
                continue
            covers = np.sum(covers_fg[new_lines,j])
            if covers < threshold:
                self.logger.debug("new lines for '%s' only cover %.1f%% fg",
                          line.id, covers * 100)
                continue
            looses = (assignments < 0) & (covers_bg[:,j] > 0.1)
            if looses.any():
                covers = np.sum(covers_bg[np.nonzero(looses)[0],j])
                self.logger.debug("new lines for '%s' would loose %d non-matching segments totalling %.1f%% bg",
                          line.id, np.count_nonzero(looses), covers * 100)
                continue
            line_count = np.count_nonzero(line_labels[j] & parent_bin)
            new_count = covers * line_count
            self.logger.debug('Black pixels before/after resegment of line "%s": %d/%d',
                      line.id, line_count, new_count)
            # combine all assigned new lines to single outline polygon
            if len(new_lines) > 1:
                self.logger.debug("joining %d new line polygons for '%s'", len(new_lines), line.id)
            new_polygon = join_polygons([new_line_polygons[i] #intersections[(i, j)]
                                         for i in new_lines], loc=line.id, scale=scale)
            new_baseline = join_baselines(self.logger, [new_polygon.intersection(new_baselines[i])
                                           for i in new_lines], loc=line.id)
            # convert back to absolute (page) coordinates:
            line_polygon = coordinates_for_segment(new_polygon.exterior.coords[:-1],
                                                   parent_image, parent_coords)
            line_polygon = polygon_for_parent(line_polygon, line.parent_object_)
            if line_polygon is None:
                self.logger.warning("Ignoring extant new polygon for line '%s'", line.id)
                return
            # annotate result:
            line.get_Coords().set_points(points_from_polygon(line_polygon))
            if new_baseline is not None:
                new_baseline = coordinates_for_segment(new_baseline.coords,
                                                       parent_image, parent_coords)
                line.set_Baseline(BaselineType(points=points_from_polygon(new_baseline)))
            line_polygons[j] = prep(new_polygon)
            # now also ensure the assigned lines do not overlap other existing lines
            for i in new_lines:
                for otherj in np.nonzero(fits_fg[i] > 0.1)[0]:
                    if j == otherj:
                        continue
                    otherline = lines[otherj]
                    self.logger.debug("subtracting new '%s' from overlapping '%s'", line.id, otherline.id)
                    other_polygon = diff_polygons(line_polygons[otherj].context, new_polygon)
                    if other_polygon.is_empty:
                        continue
                    # convert back to absolute (page) coordinates:
                    other_polygon = coordinates_for_segment(other_polygon.exterior.coords[:-1],
                                                            parent_image, parent_coords)
                    other_polygon = polygon_for_parent(other_polygon, otherline.parent_object_)
                    if other_polygon is None:
                        self.logger.warning("Ignoring extant new polygon for line '%s'", otherline.id)
                        continue
                    otherline.get_Coords().set_points(points_from_polygon(other_polygon))

def spread_dist(logger: Logger, lines, old_labels, new_labels, binarized, components, coords,
                maxdist=43, loc='', threshold=0.9):
    """redefine line coordinates by contourizing spread of connected components propagated from new labels"""
    DSAVE('seeds', [new_labels, (components>0)])
    # allocate to connected components consistently
    # (ignoring smallest components like punctuation)
    # but when there are conflicts, meet in the middle via watershed
    new_labels2 = morph.propagate_labels(components > 0, new_labels, conflict=0)
    new_labels2 = segmentation.watershed(new_labels2, markers=new_labels, mask=(components > 0))
    DSAVE('propagated', new_labels2)
    # dilate/grow labels from connected components against each other and bg
    new_labels = morph.spread_labels(new_labels2, maxdist=maxdist)
    DSAVE('spread', new_labels)
    # now propagate again to catch smallest components like punctuation
    new_labels2 = morph.propagate_labels(binarized, new_labels, conflict=0)
    new_labels2 = segmentation.watershed(new_labels2, markers=new_labels, mask=binarized)
    DSAVE('propagated-again', [new_labels2, binarized & (new_labels2==0)])
    new_labels = morph.spread_labels(new_labels2, maxdist=maxdist/4)
    DSAVE('spread-again', [new_labels, binarized])
    # find polygon hull and modify line coords
    for i, line in enumerate(lines):
        new_label = new_labels == i + 1
        old_label = old_labels[i]
        if np.equal(new_label, old_label).all():
            continue
        count = np.count_nonzero(old_label)
        if not count:
            logger.warning("skipping zero-area line '%s'", line.id)
            continue
        covers = np.count_nonzero(new_label) / count
        if covers < threshold / 3:
            logger.debug("new line for '%s' only covers %.1f%% bg",
                      line.id, covers * 100)
            continue
        count = np.count_nonzero(old_label * binarized)
        if not count:
            logger.warning("skipping binary-empty line '%s'", line.id)
            continue
        covers = np.count_nonzero(new_label * binarized) / count
        if covers < threshold:
            logger.debug("new line for '%s' only covers %.1f%% fg",
                      line.id, covers * 100)
            continue
        logger.debug('Black pixels before/after resegment of line "%s": %d/%d',
                  line.id, count, covers * count)
        contours = [contour[:,::-1] # get x,y order again
                    for contour, area in morph.find_contours(new_label)]
        #LOG.debug("joining %d subsegments for %s", len(contours), line.id)
        if len(contours) == 0:
            logger.warning("no contours for %s - keeping", line.id)
            continue
        else:
            # get alpha shape
            poly = join_polygons([make_valid(Polygon(contour))
                                  for contour in contours
                                  if len(contour) >= 4],
                                 loc=line.id, scale=maxdist)
        poly = poly.exterior.coords[:-1]
        polygon = coordinates_for_segment(poly, None, coords)
        polygon = polygon_for_parent(polygon, line.parent_object_)
        if polygon is None:
            logger.warning("Ignoring extant line for %s", line.id)
            continue
        line.get_Coords().set_points(points_from_polygon(polygon))

# zzz should go into core ocrd_utils
def baseline_of_segment(segment, coords):
    line = np.array(polygon_from_points(segment.get_Baseline().points))
    line = transform_coordinates(line, coords['transform'])
    return np.round(line).astype(np.int32)

# zzz should go into core ocrd_utils
def polygon_from_baseline(baseline, scale):
    ltr = baseline[0,0] < baseline[-1,0]
    # left-hand side if left-to-right, and vice versa
    polygon = make_valid(join_polygons([LineString(baseline).buffer(scale * (-1) ** ltr,
                                                                    single_sided=True)],
                                       scale=scale))
    return polygon
