package shop.text

class Slug(val raw: String)

class Formatter {
    fun run(slug: Slug) {
        slug.tidy()
        shorten(slug)
    }
}

fun Slug.tidy(): String = raw

fun shorten(slug: Slug): String = slug.raw
