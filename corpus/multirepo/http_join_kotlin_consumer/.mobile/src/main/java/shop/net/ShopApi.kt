package shop.net

import retrofit2.http.GET

interface ShopApi {
    @GET("v1/topics")
    suspend fun getTopics(): List<String>

    @GET("v1/unserved")
    suspend fun getUnserved(): List<String>
}
