package shop.scope

class Runner {

    fun go() {
        val helper = ::other
        helper()
        audit()
    }

    fun helper() {}

    fun audit() {}

    fun other() {}
}
