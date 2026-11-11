"""Cycles basics for a still of the exported house: render settings, camera, daylight, glass, sun."""

import math
import os

import bpy
from mathutils import Vector

import paths

SUN = {"elevation": 42, "azimuth": 200}  # the presentation look's sun (runtime.js PRESENTATION_LOOK)


def blender(p):
    x, y, z = p
    return Vector((x, -z, y))


def camera(scene, spec, width, height):
    pos, rest = spec.split(">")
    target, fov = rest.split("@")
    p = blender([float(v) for v in pos.split(",")])
    t = blender([float(v) for v in target.split(",")])
    cam = bpy.data.cameras.new("still")
    cam.sensor_fit = "VERTICAL"
    cam.angle_y = math.radians(float(fov))
    cam.clip_start = 0.05
    obj = bpy.data.objects.new("still", cam)
    scene.collection.objects.link(obj)
    d = t - p
    obj.location = p
    obj.rotation_euler = (math.pi / 2, 0.0, math.atan2(d.y, d.x) - math.pi / 2)
    pitch = math.atan2(d.z, math.hypot(d.x, d.y))
    cam.shift_y = math.tan(pitch) / (2 * math.tan(cam.angle_y / 2)) * (height / max(width, height))
    scene.camera = obj


def fix_normals(scene):
    """Stored normals against their faces (black in Cycles): shade with the faces' own normals."""
    done = set()
    for obj in scene.objects:
        me = obj.data if obj.type == "MESH" else None
        if me is None or not me.polygons or me.name in done:
            continue
        cn = me.corner_normals
        if any(sum((cn[i].vector for i in poly.loop_indices), Vector()).dot(poly.normal) < 0 for poly in me.polygons):
            with bpy.context.temp_override(object=obj, active_object=obj, selected_objects=[obj]):
                bpy.ops.mesh.customdata_custom_splitnormals_clear()
            me.shade_flat()
        done.add(me.name)


def base(scene):
    scene.render.engine = "CYCLES"
    scene.cycles.device = "GPU"
    scene.cycles.use_adaptive_sampling = True
    scene.cycles.adaptive_threshold = 0.01
    scene.cycles.use_denoising = True
    scene.cycles.denoiser = "OPENIMAGEDENOISE"
    scene.cycles.max_bounces = 12
    scene.cycles.caustics_reflective = False
    scene.cycles.caustics_refractive = False
    scene.cycles.blur_glossy = 1.0
    scene.view_settings.view_transform = "AgX"
    world = bpy.data.worlds.new("sky")
    scene.world = world
    nt = world.node_tree
    sky = nt.nodes.new("ShaderNodeTexSky")
    sky.sky_type = "MULTIPLE_SCATTERING"
    sky.sun_elevation = math.radians(SUN["elevation"])
    sky.sun_rotation = math.radians(SUN["azimuth"])
    nt.links.new(sky.outputs[0], nt.nodes["Background"].inputs[0])
    fix_normals(scene)
    for o in scene.objects:
        if o.type == "LIGHT" and o.data.type == "POINT":
            o.data.energy = 40.0
            o.data.shadow_soft_size = 0.06
        # the kit's room ceilings sit 2 cm inside the slab: the slab's underside is the ceiling
        if o.get("kind") == "ceiling":
            o.hide_render = True


def lamps_off(scene):
    """Daytime, as in listing photos: the lamps stay off."""
    for o in scene.objects:
        if o.type == "LIGHT":
            o.hide_render = True


HDRI = os.path.join(paths.ASSETS, "hdri", "meadow_8k.exr")
HDRI_SUN_PHI = 27.14  # assets/hdri/meadow_sun.json: the photo's sun, degrees from +X counterclockwise
VIEW_GAIN = 2.2


def outside(scene):
    """The view through the glass: a real photo of trees (CC0 HDRI) where the camera looks out, the sky
    and its sun for the light; the export's bare hedge cores (their leaves are instanced, not exported) go."""
    for o in scene.objects:
        if o.get("kind") in ("bush", "core", "tree"):
            o.hide_render = True
            for c in o.children_recursive:
                c.hide_render = True
    nt = scene.world.node_tree
    sky_out = nt.nodes["Background"].inputs[0].links[0].from_socket
    env = nt.nodes.new("ShaderNodeTexEnvironment")
    env.image = bpy.data.images.load(HDRI)
    coord = nt.nodes.new("ShaderNodeTexCoord")
    mapping = nt.nodes.new("ShaderNodeMapping")
    # turn the photo so its sun stands where the scene's does (compass 200 deg = math -110 deg)
    mapping.inputs["Rotation"].default_value[2] = math.radians(HDRI_SUN_PHI - (90 - SUN["azimuth"]))
    nt.links.new(coord.outputs["Generated"], mapping.inputs["Vector"])
    nt.links.new(mapping.outputs["Vector"], env.inputs["Vector"])
    gain = nt.nodes.new("ShaderNodeMixRGB")
    gain.blend_type = "MULTIPLY"
    gain.inputs[0].default_value = 1.0
    gain.inputs[2].default_value = (VIEW_GAIN, VIEW_GAIN, VIEW_GAIN, 1)
    nt.links.new(env.outputs[0], gain.inputs[1])
    # seen by the camera (through clear glass, a transparent bounce keeps the ray a camera ray) or in a
    # reflection: the photo; the light the room receives (and the sun): the sky
    path = nt.nodes.new("ShaderNodeLightPath")
    seen = nt.nodes.new("ShaderNodeMath")
    seen.operation = "MAXIMUM"
    nt.links.new(path.outputs["Is Camera Ray"], seen.inputs[0])
    nt.links.new(path.outputs["Is Glossy Ray"], seen.inputs[1])
    mix = nt.nodes.new("ShaderNodeMixRGB")
    nt.links.new(seen.outputs[0], mix.inputs[0])
    nt.links.new(sky_out, mix.inputs[1])
    nt.links.new(gain.outputs[0], mix.inputs[2])
    nt.links.new(mix.outputs[0], nt.nodes["Background"].inputs[0])


def clear_glass_material():
    m = bpy.data.materials.new("archviz-glass")
    nt = m.node_tree
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    clear = nt.nodes.new("ShaderNodeBsdfTransparent")
    clear.inputs[0].default_value = (0.93, 0.955, 0.95, 1)  # float glass's faint green
    gloss = nt.nodes.new("ShaderNodeBsdfGlossy")
    gloss.inputs["Roughness"].default_value = 0.0
    # a thin pane reflects the same from both sides (a Fresnel node turns total-internal from inside and
    # stops the sun); shadow rays pass as through clear glass
    fres = nt.nodes.new("ShaderNodeLayerWeight")
    fres.inputs["Blend"].default_value = 0.12
    path = nt.nodes.new("ShaderNodeLightPath")
    not_shadow = nt.nodes.new("ShaderNodeMath")
    not_shadow.operation = "SUBTRACT"
    not_shadow.inputs[0].default_value = 1.0
    nt.links.new(path.outputs["Is Shadow Ray"], not_shadow.inputs[1])
    fac = nt.nodes.new("ShaderNodeMath")
    fac.operation = "MULTIPLY"
    nt.links.new(fres.outputs["Fresnel"], fac.inputs[0])
    nt.links.new(not_shadow.outputs[0], fac.inputs[1])
    mix = nt.nodes.new("ShaderNodeMixShader")
    nt.links.new(fac.outputs[0], mix.inputs[0])
    nt.links.new(clear.outputs[0], mix.inputs[1])
    nt.links.new(gloss.outputs[0], mix.inputs[2])
    nt.links.new(mix.outputs[0], out.inputs[0])
    return m


def glass(scene):
    """Window and door panes: thin clear glass (transparent + a Fresnel reflection), which lets the sun
    and the camera through; the export's panes are a 55 % opaque blue-grey."""
    clear = clear_glass_material()
    n = 0
    for o in scene.objects:
        if o.type != "MESH":
            continue
        owner = o
        while owner is not None and owner.get("kind") not in ("window", "door", "building"):
            owner = owner.parent
        if owner is None:
            continue
        for slot in o.material_slots:
            p = slot.material and slot.material.node_tree and slot.material.node_tree.nodes.get("Principled BSDF")
            # a textured one is a finish seen through (glass blocks, coloured inserts), not a pane: it stays as drawn
            if p and p.inputs["Base Color"].is_linked:
                continue
            if p and (p.inputs["Alpha"].default_value < 1 or p.inputs["Transmission Weight"].default_value > 0):
                slot.material = clear
                n += 1
    print(f"glass: {n} panes")


def sun(scene, power, elevation, azimuth, sky_strength):
    """A sun lamp (sharp patches through clear glass, sampled without noise) in place of the sky's disc; returns it.
    sky_strength: the sky's fill against the sun (less fill, crisper shadows; 1 is Blender's physical sky)."""
    sky = next(n for n in scene.world.node_tree.nodes if n.bl_idname == "ShaderNodeTexSky")
    sky.sun_disc = False
    scene.world.node_tree.nodes["Background"].inputs[1].default_value = sky_strength
    sky.sun_elevation = math.radians(elevation)
    sky.sun_rotation = math.radians(azimuth)
    lamp = bpy.data.lights.new("sun", "SUN")
    lamp.energy = power
    lamp.angle = math.radians(0.53)
    lamp.color = (1.0, 0.96, 0.91)
    obj = bpy.data.objects.new("sun", lamp)
    scene.collection.objects.link(obj)
    # a sun lamp shines down its -Z: tilt it from the zenith, then turn it to the compass azimuth
    az, el = math.radians(azimuth), math.radians(elevation)
    obj.rotation_euler = (math.pi / 2 - el, 0.0, math.pi - az)
    obj.rotation_mode = "XYZ"
    return obj
