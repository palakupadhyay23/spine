package shop.make

class Widget {

    fun rebuild(): Widget {
        return make()
    }

    fun reboot(): Widget {
        return Companion.make()
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
