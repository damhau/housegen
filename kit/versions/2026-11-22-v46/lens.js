// The camera's lens on a scene page (runtime.js): the walk's, the photographer's frame of a room (photo-<n>, a shift
// lens), and the sub-pixel offset an accumulating pass draws each sample with, on top of either.
import * as THREE from "three";

/** The walk's lens: its eye above the floor (m) and its vertical field of view (°). */
export const WALK_LENS = { eye: 1.45, fov: 70 };

// The photographer's view of a room (#43), for stills: where the walk stands (its viewpoint sees the most of the room,
// a window in frame), lower (1.3 m), a 24 mm lens, level (vertical lines stay vertical) and the frame shifted down
// rather than tilted, as a shift lens does: less ceiling.
export const PHOTO_LENS = { eye: 1.3, fov: 53, shift: 0.12 };

/** The photographer's frame on the walk's camera, for a canvas of `size` (CSS pixels, a Vector2). */
export function photoLens(walk, camera, size) {
  walk.eye = PHOTO_LENS.eye;
  camera.position.y = walk.floorY + PHOTO_LENS.eye; // level: jumpTo leaves the pitch at 0
  // a frame `shift` of its height lower inside a taller one, at the lens's field of view
  const tall = 1 + 2 * PHOTO_LENS.shift;
  camera.fov = THREE.MathUtils.radToDeg(2 * Math.atan(Math.tan(THREE.MathUtils.degToRad(PHOTO_LENS.fov) / 2) * tall));
  camera.setViewOffset(size.x, size.y * tall, 0, size.y * 2 * PHOTO_LENS.shift, size.x, size.y);
  camera.updateProjectionMatrix();
}

/**
 * The walk's own lens back on its camera (a room view after a photo view on the same page, #129): its eye height, its
 * field of view, which the walk's viewpoints are chosen for, and no shift.
 */
export function walkLens(walk, camera) {
  unshift(camera);
  camera.position.y += WALK_LENS.eye - walk.eye;
  walk.eye = WALK_LENS.eye;
  camera.fov = WALK_LENS.fov;
  camera.updateProjectionMatrix();
}

/**
 * `camera` without its view offset (a shift lens), at the proportions of the frame it drew: three's setViewOffset
 * takes the taller frame's for the camera's own and clearViewOffset leaves them, so every view after a photo view was
 * drawn squeezed across (#129).
 */
export function unshift(camera) {
  if (camera.view?.enabled) camera.aspect = camera.view.width / camera.view.height;
  camera.clearViewOffset();
}

/**
 * `camera`'s frame moved by (`jx`, `jy`) pixels of a `width` x `height` drawing buffer, on top of the frame it has (a
 * shift lens's offset is kept, #129); returns the undo, which gives the frame back as it was.
 */
export function jitterFrame(camera, jx, jy, width, height) {
  const v = camera.view?.enabled ? { ...camera.view } : null, aspect = camera.aspect;
  if (v) camera.setViewOffset(v.fullWidth, v.fullHeight, v.offsetX + (jx * v.width) / width, v.offsetY + (jy * v.height) / height, v.width, v.height);
  else camera.setViewOffset(width, height, jx, jy, width, height);
  return () => {
    if (v) camera.setViewOffset(v.fullWidth, v.fullHeight, v.offsetX, v.offsetY, v.width, v.height);
    else camera.clearViewOffset();
    camera.aspect = aspect;
    camera.updateProjectionMatrix();
  };
}
