package shop.net

import retrofit2.http.GET
import retrofit2.http.POST

private const val ORDERS = "orders"

interface ShopApi {
    @GET(value = "topics")
    suspend fun getTopics(): List<String>

    @POST("carts")
    suspend fun createCart(): String

    @GET(ORDERS)
    suspend fun getOrders(): List<String>
}
