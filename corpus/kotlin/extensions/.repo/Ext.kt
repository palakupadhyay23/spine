package shop.text

import shop.remote.trim

class Slug(val raw: String)

class Formatter {
    fun run(slug: Slug) {
        slug.tidy()
        slug.trim()
        shorten(slug)
    }
}

fun Slug.tidy(): String = raw

fun shorten(slug: Slug): String = slug.raw
