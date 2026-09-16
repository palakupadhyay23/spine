package shop.make

class Widget {

    fun rebuild(): Widget {
        return make()
    }

    companion object {
        const val KIND = "widget"

        fun make(): Widget = Widget()
    }
}

class Caller {
    fun go() {
        Widget.make()
    }
}
