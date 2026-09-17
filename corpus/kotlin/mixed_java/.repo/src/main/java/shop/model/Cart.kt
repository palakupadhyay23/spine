package shop.model

class Cart : Priced {
    override fun price(): Int = 0
}

fun discount(): Int = 1
