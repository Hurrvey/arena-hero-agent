export function hitTest(entities, point, radius = 18) {
  return [...entities].reverse().find((entity) => {
    const dx = point[0] - entity.screen[0];
    const dy = point[1] - entity.screen[1];
    return dx * dx + dy * dy <= radius * radius;
  }) || null;
}
