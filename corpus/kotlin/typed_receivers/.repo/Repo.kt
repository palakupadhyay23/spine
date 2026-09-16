package shop.data

import shop.db.CartDao
import shop.log.Logger

class CartRepository(private val dao: CartDao) {

    fun load(id: String) {
        dao.fetch(id)
        val typed: Logger = build()
        typed.note(id)
        val inferred = build()
        inferred.flush()
    }

    private fun build(): Logger = TODO()
}
