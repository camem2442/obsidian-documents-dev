"""Render a source region crop from a pypdfium2 document."""


def render_region(document, region, destination):
    page = document[region["page"] - 1]
    bitmap = page.render(scale=300 / 72)
    image = bitmap.to_pil()
    x0, y0, x1, y1 = region["bbox"]
    box = tuple(round(value * 300 / 72) for value in (x0, y0, x1, y1))
    image.crop(box).save(destination)
    image.close()
    bitmap.close()
    page.close()
