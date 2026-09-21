package shop.scope

class Screen {

    fun show(m: Map<String, String>) {
        for ((key, value) in m) {
            key()
        }
    }

    fun key(): String = "k"
}
