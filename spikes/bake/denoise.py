"""#42 bake spike, step 2b: the baked lightmap through Blender's denoiser (OIDN, the compositor's Denoise node).

    uv run --no-project --python 3.11 --with bpy python -u denoise.py <lightmap.exr> <out.exr>

The compositor runs on a render: a Workbench render of an empty scene at the lightmap's size, so
only the denoise costs time. OIDN does not know the atlas's islands: the bake's margin keeps it
from bleeding the neighbours in.
"""

import sys
import time

import bpy


def main(src, dst):
    t0 = time.time()
    bpy.ops.wm.read_factory_settings(use_empty=True)
    img = bpy.data.images.load(src)
    w, h = img.size
    scene = bpy.context.scene
    scene.render.engine = "BLENDER_WORKBENCH"
    scene.render.resolution_x, scene.render.resolution_y = w, h
    scene.render.resolution_percentage = 100
    scene.render.use_compositing = True
    scene.camera = bpy.data.objects.new("cam", bpy.data.cameras.new("cam"))
    scene.collection.objects.link(scene.camera)
    tree = bpy.data.node_groups.new("denoise", "CompositorNodeTree")
    scene.compositing_node_group = tree
    n_img = tree.nodes.new("CompositorNodeImage")
    n_img.image = img
    dn = tree.nodes.new("CompositorNodeDenoise")
    out = tree.interface.new_socket("Image", in_out="OUTPUT", socket_type="NodeSocketColor")
    group_out = tree.nodes.new("NodeGroupOutput")
    tree.links.new(n_img.outputs["Image"], dn.inputs["Image"])
    tree.links.new(dn.outputs["Image"], group_out.inputs[out.name])
    scene.render.image_settings.file_format = "OPEN_EXR"
    scene.render.image_settings.color_depth = "16"
    scene.render.filepath = dst
    bpy.ops.render.render(write_still=True)
    print(f"denoised {w}x{h} in {time.time() - t0:.0f} s -> {dst}", flush=True)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
