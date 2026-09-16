package shop.cart

import shop.tax.TaxTable

interface Priced {
    fun price(): Int
}

class Cart(private val table: TaxTable, label: String) : Priced {

    private val items: MutableList<String> = mutableListOf()
    var name: String = label

    override fun price(): Int {
        return subtotal()
    }

    private fun subtotal(): Int {
        return 0
    }
}

data class Line(val sku: String, val qty: Int)

enum class Status { OPEN, CLOSED }

object Registry {
    fun reset() {}
}
