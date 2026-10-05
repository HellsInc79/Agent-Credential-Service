import bpy
import math

# Editable 3D title card starter. Run this in Blender's Scripting workspace.
bpy.ops.object.select_all(action="SELECT")
bpy.ops.object.delete(use_global=False)
scene = bpy.context.scene
scene.render.resolution_x = 1920
scene.render.resolution_y = 1080
scene.render.resolution_percentage = 100
scene.render.fps = 24
scene.render.image_settings.file_format = "PNG"
scene.render.filepath = "//renders/title-card-"
scene.render.engine = "BLENDER_EEVEE_NEXT" if bpy.app.version >= (4, 2, 0) else "BLENDER_EEVEE"

bpy.ops.mesh.primitive_plane_add(size=200, location=(0, 0, -0.5))
bpy.context.object.name = "Stage floor"
bpy.ops.object.text_add(location=(0, 0, 0))
title = bpy.context.object
title.name = "EDIT - Show title"
title.data.body = "YOUR SHOW TITLE"
title.data.align_x = "CENTER"
title.data.align_y = "CENTER"
title.data.extrude = 0.035
title.data.bevel_depth = 0.008
title.data.size = 1.25
bpy.ops.object.camera_add(location=(0, -8, 2.2), rotation=(math.radians(78), 0, 0))
scene.camera = bpy.context.object
scene.camera.name = "Title card camera"
scene.camera.data.lens = 50
for location, energy, size in [((-3, -4, 5), 1400, 5), ((3, -2, 2), 900, 4)]:
    bpy.ops.object.light_add(type="AREA", location=location)
    light = bpy.context.object
    light.data.energy = energy
    light.data.shape = "DISK"
    light.data.size = size
scene.world.color = (0.025, 0.04, 0.08)
print("Starter scene ready. Edit the title, save the .blend file, and render your title card.")
