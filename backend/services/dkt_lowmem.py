"""Memory-frugal DKT parcellation.

antspynet's desikan_killiany_tourville_labeling peaks at 56.96 GB resident /
158.66 GB commit on a 1101x250x459 clinical T1 (measured 2026-09-01 on
recon_04cfcec7). That is not the network -- the two U-Nets are small, and the
run sits at 8.8 GB when the outer model finishes predicting. The cost is what
happens next.

For each of the 63 cortical labels, antspynet resamples the model's
96x112x96 output up to the native grid and warps it back through the inverse
template transform, appending every one to a list. Only after all 63 exist does
it call ants.image_list_to_matrix() to build a dense (63 x n_voxels) array and
take an argmax down the label axis. On a 126.34 Mvox image that list alone is
63 x 505 MB = 31.8 GB, and the matrix adds a float64 copy of the same data on
top. The inner model then repeats the pattern with 34 more labels.

Nothing requires the volumes to coexist. argmax is associative: carrying a
running best-probability volume and a running best-label volume gives the same
answer while holding three arrays instead of 63. That is the only change here --
same models, same weights, same transforms, same arithmetic, evaluated in a
different order.

Everything else is a faithful port of antspynet 0.3.2's
desikan_killiany_tourville_labeling_version0, restricted to the arguments this
pipeline actually uses. Unsupported arguments raise rather than silently
returning something different from upstream.
"""
import numpy as np


class _StreamingArgmax:
    """Running argmax over probability volumes fed one at a time.

    Holds a best-value volume and a best-index volume instead of the full
    stack. np.copyto(..., where=mask) does the update in place; fancy indexing
    (best[mask] = arr[mask]) would allocate temporaries proportional to the
    number of updated voxels, which on the first few labels is most of the
    image.
    """

    def __init__(self):
        self.best = None
        self.index = None

    def add(self, i, array):
        array = np.asarray(array, dtype=np.float32)
        if self.best is None:
            self.best = array.copy()
            # 97 labels total across both passes, so uint8 is enough; it keeps
            # this volume at a quarter the size of the float32 one.
            self.index = np.zeros(array.shape, dtype=np.uint8)
            return
        mask = array > self.best
        np.copyto(self.best, array, where=mask)
        np.copyto(self.index, np.uint8(i), where=mask)

    def result(self):
        if self.index is None:
            raise RuntimeError("no probability volumes were added")
        return self.index


def desikan_killiany_tourville_labeling_lowmem(t1,
                                               do_preprocessing=True,
                                               do_denoising=True,
                                               verbose=False):
    """Drop-in replacement for antspynet's DKT labeling, version 0.

    Returns the same label image as
    antspynet.desikan_killiany_tourville_labeling(t1, do_preprocessing=...)
    with its default arguments.
    """
    import ants
    from antspynet.architectures import create_unet_model_3d
    from antspynet.utilities import get_pretrained_network
    from antspynet.utilities import get_antsxnet_data
    from antspynet.utilities import preprocess_brain_image

    if t1.dimension != 3:
        raise ValueError("Image dimension must be 3.")

    template_transform_type = "antsRegistrationSyNQuickRepro[a]"

    # ── Preprocess ───────────────────────────────────────────────────────────
    t1_preprocessed = ants.image_clone(t1)
    t1_preprocessing = None
    if do_preprocessing:
        t1_preprocessing = preprocess_brain_image(
            t1,
            truncate_intensity=(0.01, 0.99),
            brain_extraction_modality="t1",
            template="croppedMni152",
            template_transform_type=template_transform_type,
            do_bias_correction=True,
            do_denoising=do_denoising,
            verbose=verbose)
        t1_preprocessed = (t1_preprocessing["preprocessed_image"]
                           * t1_preprocessing["brain_mask"])

    def to_native(image):
        """Warp one probability volume back to the input grid."""
        if not do_preprocessing:
            return image
        return ants.apply_transforms(
            fixed=t1, moving=image,
            transformlist=t1_preprocessing["template_transforms"]["invtransforms"],
            whichtoinvert=[True], interpolator="linear", singleprecision=True,
            verbose=verbose)

    # ── Outer model: cortical labels with spatial priors ─────────────────────
    spatial_priors_file_name_path = get_antsxnet_data("priorDktLabels")
    spatial_priors = ants.image_read(spatial_priors_file_name_path)
    priors_image_list = ants.ndimage_to_list(spatial_priors)

    template_size = (96, 112, 96)
    labels = (0, 1002, 1003, *tuple(range(1005, 1032)), 1034, 1035,
              2002, 2003, *tuple(range(2005, 2032)), 2034, 2035)
    channel_size = 1 + len(priors_image_list)

    unet_model = create_unet_model_3d(
        (*template_size, channel_size),
        number_of_outputs=len(labels),
        number_of_layers=4, number_of_filters_at_base_layer=16, dropout_rate=0.0,
        convolution_kernel_size=(3, 3, 3), deconvolution_kernel_size=(2, 2, 2),
        weight_decay=1e-5, additional_options=("attentionGating"))
    unet_model.load_weights(get_pretrained_network("dktOuterWithSpatialPriors"))

    if verbose:
        print("Outer model Prediction.")

    downsampled_image = ants.resample_image(
        t1_preprocessed, template_size, use_voxels=True, interp_type=0)
    image_array = downsampled_image.numpy()
    image_array = (image_array - image_array.mean()) / image_array.std()

    batchX = np.zeros((1, *template_size, channel_size), dtype=np.float32)
    batchX[0, :, :, :, 0] = image_array
    for i in range(len(priors_image_list)):
        resampled_prior_image = ants.resample_image(
            priors_image_list[i], template_size, use_voxels=True, interp_type=0)
        batchX[0, :, :, :, i + 1] = resampled_prior_image.numpy()

    predicted_data = unet_model.predict(batchX, verbose=verbose)
    del batchX

    # Stream the 63 label volumes through the running argmax instead of
    # collecting them. This is the change that removes the 31.8 GB list and the
    # dense matrix built on top of it.
    outer = _StreamingArgmax()
    for i in range(len(labels)):
        probability_image = ants.from_numpy_like(
            np.squeeze(predicted_data[0, :, :, :, i]), downsampled_image)
        resampled_image = ants.resample_image(
            probability_image, t1_preprocessed.shape, use_voxels=True,
            interp_type=0)
        outer.add(i, to_native(resampled_image).numpy())
        del probability_image, resampled_image
    del predicted_data, unet_model

    segmentation_array = outer.result()
    del outer

    dkt_label_array = np.zeros(segmentation_array.shape, dtype=np.float32)
    for i in range(len(labels)):
        if labels[i] > 0:
            dkt_label_array[segmentation_array == i] = labels[i]
    del segmentation_array

    # ── Inner model: subcortical labels ──────────────────────────────────────
    template_size = (160, 192, 160)
    labels = (0, 4, 6, 7, 10, 11, 12, 13, 14, 15, 16, 17, 18, 24,
              26, 28, 30, 43, 44, 45, 46, 49, 50, 51, 52, 53, 54, 58,
              60, 91, 92, 630, 631, 632)

    unet_model = create_unet_model_3d(
        (*template_size, 1),
        number_of_outputs=len(labels),
        number_of_layers=4, number_of_filters_at_base_layer=8, dropout_rate=0.0,
        convolution_kernel_size=(3, 3, 3), deconvolution_kernel_size=(2, 2, 2),
        weight_decay=1e-5, additional_options=("attentionGating"))
    unet_model.load_weights(get_pretrained_network("dktInner"))

    if verbose:
        print("Prediction.")

    cropped_image = ants.crop_indices(t1_preprocessed, (12, 14, 0), (172, 206, 160))
    batchX = np.expand_dims(cropped_image.numpy(), axis=0)
    batchX = np.expand_dims(batchX, axis=-1)
    batchX = (batchX - batchX.mean()) / batchX.std()

    predicted_data = unet_model.predict(batchX, verbose=verbose)
    del batchX

    origin = cropped_image.origin
    spacing = cropped_image.spacing
    direction = cropped_image.direction

    # decrop_image needs a reference the size of the preprocessed image. Upstream
    # builds "t1_preprocessed * 0 + 1" for label 0 and "t1_preprocessed * 0" for
    # the rest; both are made once here rather than per label.
    zeros_reference = t1_preprocessed * 0
    ones_reference = zeros_reference + 1

    inner = _StreamingArgmax()
    for i in range(len(labels)):
        probability_image = ants.from_numpy(
            np.squeeze(predicted_data[0, :, :, :, i]),
            origin=origin, spacing=spacing, direction=direction)
        reference = ones_reference if i == 0 else zeros_reference
        decropped_image = ants.decrop_image(probability_image, reference)
        inner.add(i, to_native(decropped_image).numpy())
        del probability_image, decropped_image
    del predicted_data, unet_model, zeros_reference, ones_reference

    inner_segmentation_array = inner.result()
    del inner

    # The inner (subcortical) result deliberately takes precedence, matching
    # upstream's comment that it "purposely prioritize[s] the inner label
    # results".
    for i in range(len(labels)):
        if labels[i] > 0:
            dkt_label_array[inner_segmentation_array == i] = labels[i]
    del inner_segmentation_array

    return ants.from_numpy_like(dkt_label_array, t1)
